"""Runnable ADK workflow with deterministic risk and decision guardrails."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from agents.config import AgentWorkflowConfig
from agents.observability.llm_io_recorder import build_llm_io_recorder
from agents.orchestration.adk_events import (
    event_author,
    event_error,
    event_function_calls,
    event_text,
    event_token_usage,
)
from agents.orchestration.registry import (
    CRITIC_NAME,
    DECISION_SYNTHESIZER_NAME,
    FUNDAMENTAL_ANALYST_NAME,
    ROOT_AGENT_NAME,
    RETRIEVER_TOOL_NAME,
    TECHNICAL_ANALYST_NAME,
    AgentRegistry,
    build_agent_registry,
)
from agents.orchestration.trace_recorder import TraceRecorder
from agents.tools.ticker_resolver import resolve_ticker
from app.db.models import AgentRun, AgentRunStep
from app.db.repository import get_agent_run, get_agent_run_steps, save_agent_run
from app.schemas.analyze import AnalysisTrace, AnalyzeRequest, AnalyzeResponse, TraceStep
from app.services.analyzer import analyze_request

logger = logging.getLogger(__name__)

_SPECIALIST_SKIP_REASONS = {
    FUNDAMENTAL_ANALYST_NAME: "fundamental_analyst_never_reached",
    TECHNICAL_ANALYST_NAME: "technical_analyst_never_reached",
    DECISION_SYNTHESIZER_NAME: "decision_synthesizer_never_reached",
    CRITIC_NAME: "critic_never_reached",
}


class UnresolvableTickerError(ValueError):
    """Raised when the deterministic resolver cannot resolve a ticker reference."""


class GoogleWorkflowError(RuntimeError):
    """Raised when Google ADK returns or raises an unsuccessful result."""


class DecisionSynthesis(BaseModel):
    """Typed model boundary for a specialist's proposed recommendation."""

    decision: Literal["trade", "watchlist", "no_trade"]
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    entry_range: Optional[tuple[float, float]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[tuple[float, float]] = None
    risk_reward: Optional[float] = None
    position_size_eur: Optional[float] = None


@dataclass(frozen=True)
class _AdkRunResult:
    token_usage: dict[str, int]
    warnings: list[str]
    event_count: int
    observed_agents: list[str]


class _AdkExecutionError(GoogleWorkflowError):
    def __init__(self, message: str, result: _AdkRunResult) -> None:
        super().__init__(message)
        self.result = result


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
    for field_name in (
        "entry_range",
        "stop_loss",
        "take_profit",
        "risk_reward",
        "position_size_eur",
    ):
        if getattr(proposal, field_name) is None:
            setattr(proposal, field_name, getattr(deterministic, field_name))
    return proposal


RunnerFactory = Callable[[AgentRegistry, str], Iterable[Any]]


def _merge_token_usage(total: dict[str, int], event_usage: dict[str, int]) -> None:
    for field_name, value in event_usage.items():
        total[field_name] = total.get(field_name, 0) + value


def _analysis_date(request: AnalyzeRequest) -> date | None:
    return request.as_of_date


def _deterministic_fields(response: AnalyzeResponse) -> dict[str, Any]:
    return {
        "decision": response.decision,
        "entry_range": list(response.entry_range) if response.entry_range is not None else None,
        "stop_loss": response.stop_loss,
        "take_profit": list(response.take_profit) if response.take_profit is not None else None,
        "risk_reward": response.risk_reward,
        "position_size_eur": response.position_size_eur,
        "confidence": response.confidence,
        "engine_version": response.engine_version,
        "warnings": sorted(set(response.warnings)),
        "reasons": list(response.reasons),
    }


def _runner_ignores_registry(runner_factory: RunnerFactory) -> bool:
    return bool(getattr(runner_factory, "ignores_registry", False))


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
        self.llm_io_recorder = llm_io_recorder

    def analyze(self, ticker_reference: str, request: Optional[AnalyzeRequest] = None) -> AnalyzeResponse:
        run_id = str(uuid4())
        recorder = self.llm_io_recorder or build_llm_io_recorder(run_id, config=self.config)
        trace_recorder = TraceRecorder()
        warnings: list[str] = []
        started_at = time.monotonic()

        resolved_ticker = self._resolve_ticker(ticker_reference, trace_recorder)
        request = request or AnalyzeRequest(ticker=resolved_ticker)
        analysis_date = _analysis_date(request)
        response = self._run_deterministic_pipeline(request, trace_recorder)
        registry = self._build_registry(trace_recorder, warnings, today=analysis_date)

        self._record_run_metadata(recorder, run_id, resolved_ticker, registry, today=analysis_date)
        user_message = f"Analyze ticker {resolved_ticker}"
        recorder.record_user_message(user_message)

        try:
            adk_result = self._run_adk(registry, resolved_ticker, trace_recorder, response, recorder)
        except _AdkExecutionError as exc:
            self._record_unreached_specialists(trace_recorder, exc.result.observed_agents)
            final_warnings = list(response.warnings) + warnings + exc.result.warnings
            response.warnings = final_warnings
            partial_trace = self._build_trace(
                run_id=run_id,
                ticker=resolved_ticker,
                trace_recorder=trace_recorder,
                response=response,
                token_usage=exc.result.token_usage,
                warnings=final_warnings,
                total_latency_ms=(time.monotonic() - started_at) * 1000,
                adk_event_count=exc.result.event_count,
                observed_agents=exc.result.observed_agents,
            )
            self._persist_trace(partial_trace)
            recorder.record_summary(
                {
                    "event_count": exc.result.event_count,
                    "total_token_usage": exc.result.token_usage,
                    "final_decision": response.decision,
                    "deterministic_fields": _deterministic_fields(response),
                    "observed_agents": exc.result.observed_agents,
                    "warnings": final_warnings,
                    "error": str(exc),
                    "wall_clock_ms": partial_trace.total_latency_ms,
                }
            )
            if getattr(recorder, "enabled", False):
                logger.info("llm_io_captured run_id=%s directory=%s", run_id, recorder.run_directory)
            raise GoogleWorkflowError(str(exc)) from exc

        self._record_unreached_specialists(trace_recorder, adk_result.observed_agents)
        final_warnings = list(response.warnings) + warnings + adk_result.warnings
        response.warnings = final_warnings
        total_latency_ms = (time.monotonic() - started_at) * 1000
        trace = self._build_trace(
            run_id=run_id,
            ticker=resolved_ticker,
            trace_recorder=trace_recorder,
            response=response,
            token_usage=adk_result.token_usage,
            warnings=final_warnings,
            total_latency_ms=total_latency_ms,
            adk_event_count=adk_result.event_count,
            observed_agents=adk_result.observed_agents,
        )
        response.trace_id = run_id
        response.trace = trace
        self._persist_trace(trace)
        recorder.record_summary(
            {
                "event_count": adk_result.event_count,
                "total_token_usage": adk_result.token_usage,
                "final_decision": response.decision,
                "deterministic_fields": _deterministic_fields(response),
                "observed_agents": adk_result.observed_agents,
                "warnings": final_warnings,
                "wall_clock_ms": total_latency_ms,
            }
        )
        if getattr(recorder, "enabled", False):
            logger.info("llm_io_captured run_id=%s directory=%s", run_id, recorder.run_directory)
        return response

    def _resolve_ticker(self, ticker_reference: str, trace_recorder: TraceRecorder) -> str:
        started_at = time.monotonic()
        resolved = resolve_ticker(ticker_reference)
        if not resolved.is_valid:
            raise UnresolvableTickerError(resolved.error or "Ticker could not be resolved.")
        trace_recorder.record_completed(
            "input_resolver",
            latency_ms=(time.monotonic() - started_at) * 1000,
            output={"ticker": resolved.ticker},
        )
        return resolved.ticker

    def _run_deterministic_pipeline(
        self,
        request: AnalyzeRequest,
        trace_recorder: TraceRecorder,
    ) -> AnalyzeResponse:
        started_at = time.monotonic()
        response = analyze_request(request, engine=self.engine)
        trace_recorder.record_completed(
            "deterministic_pipeline",
            latency_ms=(time.monotonic() - started_at) * 1000,
            output={
                "decision": response.decision,
                "confidence": response.confidence,
                "engine_version": response.engine_version,
                "warnings": list(response.warnings),
                "reason_count": len(response.reasons),
            },
        )
        return response

    def _build_registry(
        self,
        trace_recorder: TraceRecorder,
        warnings: list[str],
        *,
        today: date | None,
    ) -> AgentRegistry:
        registry = build_agent_registry(config=self.config, engine=self.engine, today=today)
        if _runner_ignores_registry(self.runner_factory):
            return registry
        if RETRIEVER_TOOL_NAME not in registry.tools:
            trace_recorder.record_skipped(RETRIEVER_TOOL_NAME, "retriever_tool_unavailable")
            warnings.append("retriever_tool_unavailable")
        return registry

    def _run_adk(
        self,
        registry: AgentRegistry,
        ticker: str,
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        recorder=None,
    ) -> _AdkRunResult:
        token_usage: dict[str, int] = {}
        warnings: list[str] = []
        observed_agents: list[str] = []
        event_count = 0
        previous_event_at = time.monotonic()

        try:
            events = self.runner_factory(registry, ticker)
            for event in events:
                event_count += 1
                if recorder is not None:
                    recorder.record_event(event_count - 1, event)

                now = time.monotonic()
                latency_ms = (now - previous_event_at) * 1000
                previous_event_at = now

                author = event_author(event)
                if author not in observed_agents:
                    observed_agents.append(author)
                tool_calls = event_function_calls(event)
                text = event_text(event)
                usage = event_token_usage(event)
                _merge_token_usage(token_usage, usage)

                error_detail = event_error(event)
                if error_detail is not None:
                    warnings.append(f"agent_narration_degraded: {error_detail}")
                    trace_recorder.record_degraded(
                        author,
                        "agent_narration_degraded",
                        detail=error_detail,
                        tool_calls=tool_calls,
                        output={"text": text} if text else None,
                        latency_ms=latency_ms,
                        token_usage=usage,
                    )
                    continue

                if author == DECISION_SYNTHESIZER_NAME:
                    self._record_decision_synthesizer_step(
                        trace_recorder=trace_recorder,
                        response=response,
                        warnings=warnings,
                        text=text,
                        tool_calls=tool_calls,
                        latency_ms=latency_ms,
                        token_usage=usage,
                    )
                    continue

                trace_recorder.record_completed(
                    author,
                    tool_calls=tool_calls,
                    output={"text": text} if text else None,
                    latency_ms=latency_ms,
                    token_usage=usage,
                )
            return _AdkRunResult(
                token_usage=token_usage,
                warnings=warnings,
                event_count=event_count,
                observed_agents=observed_agents,
            )
        except Exception as exc:
            detail = str(exc)[:500]
            trace_recorder.record_failed(
                ROOT_AGENT_NAME,
                "adk_runner_exception",
                detail=detail,
            )
            logger.exception("google_adk_workflow_failed: ticker=%s", ticker)
            result = _AdkRunResult(
                token_usage=token_usage,
                warnings=warnings,
                event_count=event_count,
                observed_agents=observed_agents,
            )
            raise _AdkExecutionError(f"ADK execution failed: {exc}", result) from exc

    def _record_decision_synthesizer_step(
        self,
        *,
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        warnings: list[str],
        text: str,
        tool_calls: list[str],
        latency_ms: float,
        token_usage: dict[str, int],
    ) -> None:
        synthesis = self._parse_synthesis(text)
        if synthesis is None:
            trace_recorder.record_degraded(
                DECISION_SYNTHESIZER_NAME,
                "unparsable_synthesis",
                tool_calls=tool_calls,
                output={"text": text} if text else None,
                latency_ms=latency_ms,
                token_usage=token_usage,
            )
            return

        try:
            guarded = enforce_downgrade_only(synthesis, response)
        except ValueError as exc:
            detail = str(exc)
            warnings.append(f"guardrail_rejected: {detail}")
            trace_recorder.record_degraded(
                DECISION_SYNTHESIZER_NAME,
                "guardrail_rejected",
                detail=detail,
                tool_calls=tool_calls,
                output={"text": text} if text else None,
                latency_ms=latency_ms,
                token_usage=token_usage,
            )
            return

        was_downgraded = (
            guarded.decision != response.decision or guarded.confidence != response.confidence
        )
        response.decision = guarded.decision
        response.confidence = guarded.confidence
        trace_recorder.record_completed(
            DECISION_SYNTHESIZER_NAME,
            tool_calls=tool_calls,
            output={
                "text": text,
                "guardrail": "downgraded" if was_downgraded else "unchanged",
            },
            latency_ms=latency_ms,
            token_usage=token_usage,
        )

    def _parse_synthesis(self, text: str) -> DecisionSynthesis | None:
        if not text.strip():
            return None
        try:
            return DecisionSynthesis.model_validate_json(text)
        except ValidationError:
            return None

    def _record_unreached_specialists(
        self,
        trace_recorder: TraceRecorder,
        observed_agents: list[str],
    ) -> None:
        observed = set(observed_agents)
        for agent_name, reason in _SPECIALIST_SKIP_REASONS.items():
            if agent_name not in observed:
                trace_recorder.record_skipped(agent_name, reason)

    def _record_run_metadata(
        self,
        recorder,
        run_id: str,
        ticker: str,
        registry: AgentRegistry,
        *,
        today: date | None,
    ) -> None:
        git_sha = os.getenv("GITHUB_SHA")
        recorder.record_run_config(
            {
                "run_id": run_id,
                "ticker": ticker,
                "today": today.isoformat() if today is not None else None,
                "model": self.config.model_name,
                "temperature": self.config.temperature,
                "timeout_seconds": self.config.timeout_seconds,
                "max_retries": self.config.max_retries,
                "pipeline_mode": self.config.pipeline_mode,
                "git_sha": git_sha,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        recorder.record_agent_prompts(registry)

    def _build_trace(
        self,
        *,
        run_id: str,
        ticker: str,
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        token_usage: dict[str, int],
        warnings: list[str],
        total_latency_ms: float,
        adk_event_count: int,
        observed_agents: list[str],
    ) -> AnalysisTrace:
        return AnalysisTrace(
            run_id=run_id,
            ticker=ticker,
            steps=trace_recorder.steps(),
            final_decision=response.decision,
            total_latency_ms=total_latency_ms,
            token_usage=token_usage,
            warnings=warnings,
            entered_agent_layer=adk_event_count > 0,
            adk_event_count=adk_event_count,
            observed_agents=observed_agents,
        )

    @staticmethod
    def _default_runner_factory(registry: AgentRegistry, ticker: str) -> Iterable[Any]:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part

        session_service = InMemorySessionService()
        session = asyncio.run(
            session_service.create_session(
                app_name="forseti",
                user_id=ticker,
            )
        )
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
            entered_agent_layer=trace.entered_agent_layer,
            adk_event_count=trace.adk_event_count,
            observed_agents=trace.observed_agents,
        )
        steps = [AgentRunStep(run_id=trace.run_id, **step.model_dump()) for step in trace.steps]
        save_agent_run(run, steps, engine=self.engine)


def load_trace(run_id: str, engine=None) -> Optional[AnalysisTrace]:
    run = get_agent_run(run_id, engine=engine)
    if run is None:
        return None
    steps = [
        TraceStep(**step.model_dump(exclude={"id", "run_id"}))
        for step in get_agent_run_steps(run_id, engine=engine)
    ]
    final_decision = str(run.final_decision) if run.final_decision is not None else None
    return AnalysisTrace(
        run_id=run.run_id,
        ticker=run.ticker,
        steps=steps,
        final_decision=final_decision,
        total_latency_ms=run.total_latency_ms,
        token_usage=run.token_usage,
        warnings=run.warnings,
        entered_agent_layer=run.entered_agent_layer,
        adk_event_count=run.adk_event_count,
        observed_agents=run.observed_agents,
    )
