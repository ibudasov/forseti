"""Runnable ADK workflow with deterministic risk and decision guardrails."""
from __future__ import annotations

import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from agents.config import AgentWorkflowConfig
from agents.orchestration.registry import AgentRegistry, build_agent_registry
from agents.tools.ticker_resolver import resolve_ticker
from app.db.models import AgentRun, AgentRunStep
from app.db.repository import get_agent_run, get_agent_run_steps, save_agent_run
from app.schemas.analyze import AnalysisTrace, AnalyzeRequest, AnalyzeResponse, TraceStep
from app.services.analyzer import analyze_request
from agents.observability.llm_io_recorder import (
    NullLlmIoRecorder,
    build_llm_io_recorder,
)

logger = logging.getLogger(__name__)


class UnresolvableTickerError(ValueError):
    """Raised when the deterministic resolver cannot resolve a ticker reference."""


class GoogleWorkflowError(RuntimeError):
    """Raised when Google ADK returns or raises an unsuccessful result."""


class DecisionSynthesis(BaseModel):
    """Typed model boundary for a specialist's proposed recommendation."""

    decision: str
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    entry_range: Optional[tuple[float, float]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[tuple[float, float]] = None
    risk_reward: Optional[float] = None
    position_size_eur: Optional[float] = None


def validate_risk_output(proposal: DecisionSynthesis, deterministic: AnalyzeResponse) -> None:
    """Reject a specialist proposal that fabricates or changes trade numbers."""
    fields = (
        "entry_range",
        "stop_loss",
        "take_profit",
        "risk_reward",
        "position_size_eur",
    )
    for field_name in fields:
        proposed_value = getattr(proposal, field_name)
        deterministic_value = getattr(deterministic, field_name)
        if proposed_value is not None and proposed_value != deterministic_value:
            raise ValueError(f"agent_output_changed_risk_value: {field_name}")


def _decision_rank(decision: str) -> int:
    return {"no_trade": 0, "watchlist": 1, "trade": 2}[decision]


def enforce_downgrade_only(proposal: DecisionSynthesis, deterministic: AnalyzeResponse) -> DecisionSynthesis:
    """Return a guarded proposal whose decision and confidence never upgrade."""
    validate_risk_output(proposal, deterministic)
    if _decision_rank(proposal.decision) > _decision_rank(deterministic.decision):
        proposal.decision = deterministic.decision
    proposal.confidence = min(proposal.confidence, deterministic.confidence)
    return proposal


RunnerFactory = Callable[[AgentRegistry, str], Iterable[Any]]


def _event_value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _token_usage_from_event(event: Any) -> dict[str, int]:
    usage_metadata = _event_value(event, "usage_metadata")
    if usage_metadata is None:
        return {}

    usage: dict[str, int] = {}
    field_names = (
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "cached_content_token_count",
        "thoughts_token_count",
    )
    for field_name in field_names:
        value = _event_value(usage_metadata, field_name)
        if isinstance(value, int):
            usage[field_name] = value
    return usage


def _merge_token_usage(total: dict[str, int], event_usage: dict[str, int]) -> None:
    for field_name, value in event_usage.items():
        total[field_name] = total.get(field_name, 0) + value


def _first_line(detail: Any) -> str:
    return str(detail).strip().splitlines()[0] if str(detail).strip() else str(detail)


def _event_author(event: Any) -> str:
    return str(_event_value(event, "author") or "unknown_agent")


def _event_status(event: Any) -> str:
    if _event_value(event, "error_code"):
        return "failed"
    if _event_value(event, "error_message"):
        return "degraded"
    return "completed"


def _event_tool_calls(event: Any) -> list[str]:
    calls: list[str] = []
    content = _event_value(event, "content")
    parts = _event_value(content, "parts") or []
    for part in parts:
        function_call = _event_value(part, "function_call")
        name = _event_value(function_call, "name")
        if name:
            calls.append(str(name))
    return calls


class AgenticAnalysisWorkflow:
    """Application-level port for linear and ADK-backed analysis execution."""

    def __init__(
        self,
        config: AgentWorkflowConfig,
        engine=None,
        runner_factory: Optional[RunnerFactory] = None,
        llm_io_recorder=None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.runner_factory = runner_factory or self._default_runner_factory
        self.llm_io_recorder = llm_io_recorder or build_llm_io_recorder(
            str(uuid4()), config=config
        )

    def analyze(self, ticker_reference: str, request: Optional[AnalyzeRequest] = None) -> AnalyzeResponse:
        resolved = resolve_ticker(ticker_reference)
        if not resolved.is_valid:
            raise UnresolvableTickerError(resolved.error or "Ticker could not be resolved.")

        request = request or AnalyzeRequest(ticker=resolved.ticker)
        run_id = str(uuid4())
        started_at = time.monotonic()
        steps: list[TraceStep] = []
        response = analyze_request(request, engine=self.engine)

        registry = build_agent_registry(config=self.config, engine=self.engine)
        recorder = self.llm_io_recorder
        recorder.record_run_config({
            "run_id": run_id,
            "ticker": resolved.ticker,
            "model": self.config.model_name,
            "temperature": self.config.temperature,
            "timeout_seconds": self.config.timeout_seconds,
            "pipeline_mode": self.config.pipeline_mode,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        recorder.record_agent_prompts(registry)
        user_message = f"Analyze ticker {resolved.ticker}"
        recorder.record_user_message(user_message)
        adk_warnings: list[str] = []
        try:
            token_usage = self._run_adk(
                registry, resolved.ticker, run_id, steps, response, adk_warnings, recorder
            )
        except Exception as exc:
            recorder.record_summary({
                "event_count": len(steps),
                "total_token_usage": {},
                "final_decision": response.decision,
                "warnings": adk_warnings,
                "error": str(exc),
                "wall_clock_ms": (time.monotonic() - started_at) * 1000,
            })
            raise
        response.warnings = list(response.warnings) + adk_warnings
        warning_list = list(response.warnings)
        total_latency_ms = (time.monotonic() - started_at) * 1000
        trace = AnalysisTrace(
            run_id=run_id,
            ticker=resolved.ticker,
            steps=steps,
            final_decision=response.decision,
            total_latency_ms=total_latency_ms,
            token_usage=token_usage,
            warnings=warning_list,
            entered_agent_layer=True,
            adk_event_count=len(steps),
            observed_agents=list(dict.fromkeys(step.agent_name for step in steps)),
        )
        response.trace_id = run_id
        response.trace = trace
        self._persist_trace(trace)
        recorder.record_summary({
            "event_count": len(steps),
            "total_token_usage": token_usage,
            "final_decision": response.decision,
            "warnings": warning_list,
            "wall_clock_ms": total_latency_ms,
        })
        if getattr(recorder, "enabled", False):
            logger.info("llm_io_captured run_id=%s directory=%s", run_id, recorder.run_directory)
        return response

    def _initial_steps(self, ticker: str) -> list[TraceStep]:
        return []

    def _run_adk(
        self,
        registry: AgentRegistry,
        ticker: str,
        run_id: str,
        steps: list[TraceStep],
        response: AnalyzeResponse,
        warnings: list[str],
        recorder=None,
    ) -> dict[str, int]:
        if self.runner_factory is None:
            return {}
        started_at = time.monotonic()
        token_usage: dict[str, int] = {}
        try:
            events = self.runner_factory(registry, ticker)
            for event in events:
                if recorder is not None:
                    recorder.record_event(len(steps), event)
                event_status = _event_status(event)
                event_author = _event_author(event)
                # The narration layer never produces trade numbers, so a model-level
                # error degrades the memo but must not fail the deterministic answer.
                error_code = _event_value(event, "error_code")
                error_message = _event_value(event, "error_message")
                if error_code or error_message:
                    detail = _first_line(error_message or error_code)
                    warnings.append(f"agent_narration_degraded: {detail}")
                _merge_token_usage(token_usage, _token_usage_from_event(event))
                steps.append(
                    TraceStep(
                        sequence=len(steps) + 1,
                        agent_name=event_author,
                        status=event_status,
                        tool_calls=_event_tool_calls(event),
                        token_usage=_token_usage_from_event(event),
                        output={
                            "error_code": error_code,
                            "error_message": _first_line(error_message) if error_message else None,
                        }
                        if error_code or error_message
                        else None,
                    )
                )
            if steps:
                steps[-1].latency_ms = (time.monotonic() - started_at) * 1000
            return token_usage
        except Exception as exc:
            logger.exception("google_adk_workflow_failed: ticker=%s", ticker)
            raise GoogleWorkflowError(f"ADK execution failed: {exc}") from exc

    @staticmethod
    def _default_runner_factory(registry: AgentRegistry, ticker: str) -> Iterable[Any]:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part

        session_service = InMemorySessionService()
        session = asyncio.run(session_service.create_session(
            app_name="forseti",
            user_id=ticker,
        ))
        runner = Runner(
            app_name="forseti",
            agent=registry.root_agent,
            session_service=session_service,
        )
        return runner.run(
            user_id=ticker,
            session_id=session.id,
            new_message=Content(role="user", parts=[Part(text=f"Analyze ticker {ticker}")]),
        )

    def _persist_trace(self, trace: AnalysisTrace) -> None:
        run = AgentRun(
            run_id=trace.run_id,
            ticker=trace.ticker,
            created_at=datetime.now(timezone.utc),
            final_decision=trace.final_decision,
            total_latency_ms=trace.total_latency_ms,
            token_usage=trace.token_usage,
            warnings=trace.warnings,
        )
        steps = [AgentRunStep(run_id=trace.run_id, **step.model_dump()) for step in trace.steps]
        save_agent_run(run, steps, engine=self.engine)


def load_trace(run_id: str, engine=None) -> Optional[AnalysisTrace]:
    run = get_agent_run(run_id, engine=engine)
    if run is None:
        return None
    steps = [TraceStep(**step.model_dump(exclude={"id", "run_id"})) for step in get_agent_run_steps(run_id, engine=engine)]
    return AnalysisTrace(
        run_id=run.run_id,
        ticker=run.ticker,
        steps=steps,
        final_decision=run.final_decision.value if run.final_decision else None,
        total_latency_ms=run.total_latency_ms,
        token_usage=run.token_usage,
        warnings=run.warnings,
        entered_agent_layer=True,
        adk_event_count=len(steps),
        observed_agents=list(dict.fromkeys(step.agent_name for step in steps)),
    )
