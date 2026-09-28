"""Runnable ADK workflow with deterministic risk and decision guardrails."""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import logging
import os
import time
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Literal, Optional, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from agents.config import HARD_RULES_TEXT
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
from app.db.repository import get_agent_run, get_agent_run_steps, get_latest_bars, save_agent_run
from app.rag.embedding import EmbeddingClient
from app.schemas.analyze import AnalysisTrace, AnalyzeRequest, AnalyzeResponse, TraceStep
from app.schemas.critique import CritiqueObjection, CritiqueResult, DraftRecommendation
from app.schemas.fundamentals import FundamentalAssessmentResult
from app.services.critic import Critic, RetryableModelError
from app.services.critic_policy import apply_critic_policy
from app.services.critic_rules import evaluate_deterministic_objections
from app.services.critique_context import (
    build_analyst_views,
    build_critique_request,
    build_draft_from_response,
)
from app.services.fundamental_analyst import FundamentalAnalyst
from app.services.fundamental_context import build_fundamental_analysis_request
from app.services.fundamental_policy import apply_fundamental_policy
from app.services.risk import RiskConfig, RiskDowngrade, calculate_risk_levels
from app.services.time_stop import calculate_time_stop_at
from app.services.analyzer import analyze_request
from app.settings import get_settings

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


@dataclass
class _PendingTraceStep:
    status: Literal["completed", "degraded", "skipped"]
    tool_calls: list[str]
    output: dict[str, Any] | None
    latency_ms: float
    token_usage: dict[str, int]
    retries: int = 0
    reason: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class _DecisionRevisionResult:
    synthesis: DecisionSynthesis | None
    raw_output: str
    token_usage: dict[str, int]
    latency_ms: float
    error_reason: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class _CriticStepResult:
    step: _PendingTraceStep
    synthesis_retries: int = 0
    additional_token_usage: dict[str, int] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class _ModelOutput:
    text: str
    token_usage: dict[str, int]


class _DecisionRevisionModelPort(Protocol):
    model_name: str

    def generate(self, prompt: str) -> _ModelOutput:
        """Return one raw JSON response for the revision request."""


class _GeminiDecisionRevisionModel:
    def __init__(
        self,
        *,
        model_name: str,
        vertex_project: str | None,
        vertex_location: str,
        temperature: float,
    ) -> None:
        self.model_name = model_name
        self._vertex_project = vertex_project
        self._vertex_location = vertex_location
        self._temperature = temperature

    def generate(self, prompt: str) -> _ModelOutput:
        if not self._vertex_project:
            raise RetryableModelError("vertex_project_not_configured")
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel

            vertexai.init(project=self._vertex_project, location=self._vertex_location)
            response = GenerativeModel(self.model_name).generate_content(
                prompt,
                generation_config={
                    "temperature": self._temperature,
                    "response_mime_type": "application/json",
                },
            )
        except Exception as exc:
            raise RetryableModelError(str(exc)) from exc
        return _ModelOutput(text=response.text, token_usage=_token_usage(response))


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


def _downgrade_only_outcome(
    *,
    baseline_decision: str,
    baseline_confidence: float,
    proposed_decision: str,
    proposed_confidence: float,
) -> tuple[Literal["trade", "watchlist", "no_trade"], float]:
    decision = cast(Literal["trade", "watchlist", "no_trade"], proposed_decision)
    if _decision_rank(decision) > _decision_rank(baseline_decision):
        decision = cast(Literal["trade", "watchlist", "no_trade"], baseline_decision)
    confidence = min(proposed_confidence, baseline_confidence)
    return decision, confidence


def enforce_downgrade_only(proposal: DecisionSynthesis, deterministic: AnalyzeResponse) -> DecisionSynthesis:
    """Return a guarded proposal whose decision and confidence never upgrade."""
    validate_risk_output(proposal, deterministic)
    proposal.decision, proposal.confidence = _downgrade_only_outcome(
        baseline_decision=deterministic.decision,
        baseline_confidence=deterministic.confidence,
        proposed_decision=proposal.decision,
        proposed_confidence=proposal.confidence,
    )
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


def _token_usage(response: Any) -> dict[str, int]:
    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is None:
        return {}
    return {
        key: int(value)
        for key in (
            "prompt_token_count",
            "candidates_token_count",
            "total_token_count",
            "thoughts_token_count",
        )
        if (value := getattr(usage_metadata, key, None)) is not None
    }


RunnerFactory = Callable[[AgentRegistry, str], Iterable[Any]]


def _merge_token_usage(total: dict[str, int], event_usage: dict[str, int]) -> None:
    for field_name, value in event_usage.items():
        total[field_name] = total.get(field_name, 0) + value


def _merged_token_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    merged = dict(left)
    _merge_token_usage(merged, right)
    return merged


def _objection_counts_by_severity(objections: Iterable[CritiqueObjection]) -> dict[str, int]:
    return dict(Counter(objection.severity for objection in objections))


def _update_critic_trace_output(step: _PendingTraceStep, effect: Any) -> None:
    if step.output is None:
        return
    step.output["baseline_decision"] = effect.baseline_decision
    step.output["final_decision"] = effect.final_decision
    step.output["reason_codes"] = list(effect.reason_codes)


def _analysis_date(request: AnalyzeRequest) -> date | None:
    return request.as_of_date


def _fundamental_agent_mode() -> Literal["off", "shadow", "enforced"]:
    mode = get_settings().FUNDAMENTAL_AGENT_MODE.strip().lower()
    if mode not in {"off", "shadow", "enforced"}:
        raise ValueError("FUNDAMENTAL_AGENT_MODE must be one of ('off', 'shadow', 'enforced').")
    return cast(Literal["off", "shadow", "enforced"], mode)


def _critic_mode() -> Literal["off", "shadow", "enforced"]:
    mode = get_settings().CRITIC_MODE.strip().lower()
    if mode not in {"off", "shadow", "enforced"}:
        raise ValueError("CRITIC_MODE must be one of ('off', 'shadow', 'enforced').")
    return cast(Literal["off", "shadow", "enforced"], mode)


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


MAX_CRITIC_REVISIONS = 1


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
        embedding_client: Optional[EmbeddingClient] = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.runner_factory = runner_factory or self._default_runner_factory
        self.llm_io_recorder = llm_io_recorder
        self.embedding_client = embedding_client

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
        fundamental_assessment = self._apply_fundamental_policy_if_enabled(
            run_id=run_id,
            request=request,
            response=response,
            trace_recorder=trace_recorder,
        )
        registry = self._build_registry(trace_recorder, warnings, today=analysis_date)

        self._record_run_metadata(recorder, run_id, resolved_ticker, registry, today=analysis_date)
        user_message = f"Analyze ticker {resolved_ticker}"
        recorder.record_user_message(user_message)

        try:
            adk_result = self._run_adk(
                registry,
                resolved_ticker,
                request=request,
                run_id=run_id,
                trace_recorder=trace_recorder,
                response=response,
                baseline=build_draft_from_response(response, source="deterministic"),
                fundamental_assessment=fundamental_assessment,
                recorder=recorder,
            )
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
        registry_kwargs = {
            "config": self.config,
            "engine": self.engine,
            "today": today,
        }
        if self.embedding_client is not None:
            registry_kwargs["embedding_client"] = self.embedding_client
        registry = build_agent_registry(**registry_kwargs)
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
        *,
        request: AnalyzeRequest,
        run_id: str,
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        baseline: DraftRecommendation,
        fundamental_assessment: FundamentalAssessmentResult | None,
        recorder=None,
    ) -> _AdkRunResult:
        token_usage: dict[str, int] = {}
        warnings: list[str] = []
        observed_agents: list[str] = []
        event_count = 0
        previous_event_at = time.monotonic()
        pending_synthesis: _PendingTraceStep | None = None

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
                    self._flush_pending_synthesis(trace_recorder, pending_synthesis)
                    pending_synthesis = None
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
                    pending_synthesis = self._build_decision_synthesizer_step(
                        response=response,
                        warnings=warnings,
                        text=text,
                        tool_calls=tool_calls,
                        latency_ms=latency_ms,
                        token_usage=usage,
                    )
                    continue

                if author == CRITIC_NAME:
                    critic_result = self._record_critic_policy_step(
                        ticker=ticker,
                        request=request,
                        run_id=run_id,
                        trace_recorder=trace_recorder,
                        response=response,
                        baseline=baseline,
                        fundamental_assessment=fundamental_assessment,
                        warnings=warnings,
                        tool_calls=tool_calls,
                        latency_ms=latency_ms,
                        token_usage=usage,
                        pending_synthesis=pending_synthesis,
                    )
                    _merge_token_usage(token_usage, critic_result.additional_token_usage)
                    self._flush_pending_synthesis(
                        trace_recorder,
                        pending_synthesis,
                        retries=critic_result.synthesis_retries,
                    )
                    self._record_pending_step(
                        trace_recorder,
                        CRITIC_NAME,
                        critic_result.step,
                    )
                    pending_synthesis = None
                    continue

                self._flush_pending_synthesis(trace_recorder, pending_synthesis)
                pending_synthesis = None

                trace_recorder.record_completed(
                    author,
                    tool_calls=tool_calls,
                    output={"text": text} if text else None,
                    latency_ms=latency_ms,
                    token_usage=usage,
                )
            self._flush_pending_synthesis(trace_recorder, pending_synthesis)
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

    def _build_decision_synthesizer_step(
        self,
        *,
        response: AnalyzeResponse,
        warnings: list[str],
        text: str,
        tool_calls: list[str],
        latency_ms: float,
        token_usage: dict[str, int],
    ) -> _PendingTraceStep:
        synthesis = self._parse_synthesis(text)
        if synthesis is None:
            return _PendingTraceStep(
                status="degraded",
                reason="unparsable_synthesis",
                tool_calls=tool_calls,
                output={"text": text} if text else None,
                latency_ms=latency_ms,
                token_usage=token_usage,
            )

        try:
            validate_risk_output(synthesis, response)
        except ValueError as exc:
            detail = str(exc)
            warnings.append(f"guardrail_rejected: {detail}")
            return _PendingTraceStep(
                status="degraded",
                reason="guardrail_rejected",
                detail=detail,
                tool_calls=tool_calls,
                output={"text": text} if text else None,
                latency_ms=latency_ms,
                token_usage=token_usage,
            )

        was_downgraded = self._apply_downgrade_only_transition(
            response=response,
            proposed_decision=synthesis.decision,
            proposed_confidence=synthesis.confidence,
        )
        for field_name in (
            "entry_range",
            "stop_loss",
            "take_profit",
            "risk_reward",
            "position_size_eur",
        ):
            proposed_value = getattr(synthesis, field_name)
            if proposed_value is None:
                continue
            setattr(response, field_name, proposed_value)
        return _PendingTraceStep(
            status="completed",
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

    def _apply_downgrade_only_transition(
        self,
        *,
        response: AnalyzeResponse,
        proposed_decision: str,
        proposed_confidence: float,
    ) -> bool:
        next_decision, next_confidence = _downgrade_only_outcome(
            baseline_decision=response.decision,
            baseline_confidence=response.confidence,
            proposed_decision=proposed_decision,
            proposed_confidence=proposed_confidence,
        )
        was_changed = (
            next_decision != response.decision or next_confidence != response.confidence
        )
        response.decision = next_decision
        response.confidence = next_confidence
        return was_changed

    def _flush_pending_synthesis(
        self,
        trace_recorder: TraceRecorder,
        pending_step: _PendingTraceStep | None,
        *,
        retries: int | None = None,
    ) -> None:
        if pending_step is None:
            return None
        if retries is not None:
            pending_step.retries = retries
        self._record_pending_step(trace_recorder, DECISION_SYNTHESIZER_NAME, pending_step)
        return None

    def _record_pending_step(
        self,
        trace_recorder: TraceRecorder,
        agent_name: str,
        step: _PendingTraceStep,
    ) -> None:
        if step.status == "completed":
            trace_recorder.record_completed(
                agent_name,
                tool_calls=step.tool_calls,
                output=step.output,
                latency_ms=step.latency_ms,
                token_usage=step.token_usage,
                retries=step.retries,
            )
            return
        if step.status == "skipped":
            trace_recorder.record_skipped(
                agent_name,
                step.reason or "skipped",
            )
            return
        trace_recorder.record_degraded(
            agent_name,
            step.reason or "degraded",
            detail=step.detail,
            tool_calls=step.tool_calls,
            output=step.output,
            latency_ms=step.latency_ms,
            token_usage=step.token_usage,
            retries=step.retries,
        )

    def _record_critic_policy_step(
        self,
        *,
        ticker: str,
        request: AnalyzeRequest,
        run_id: str,
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        baseline: DraftRecommendation,
        fundamental_assessment: FundamentalAssessmentResult | None,
        warnings: list[str],
        tool_calls: list[str],
        latency_ms: float,
        token_usage: dict[str, int],
        pending_synthesis: _PendingTraceStep | None,
    ) -> _CriticStepResult:
        del trace_recorder
        mode = _critic_mode()
        if mode == "off":
            return _CriticStepResult(
                step=_PendingTraceStep(
                    status="skipped",
                    reason="critic_mode_off",
                    tool_calls=[],
                    output={"reason": "critic_mode_off"},
                    latency_ms=latency_ms,
                    token_usage=token_usage,
                )
            )

        critique_request = build_critique_request(
            ticker=ticker,
            run_id=run_id,
            draft=build_draft_from_response(
                response,
                memo=self._synthesizer_memo(pending_synthesis),
                source="decision_synthesizer",
            ),
            analyst_views=build_analyst_views(fundamental=fundamental_assessment),
            deterministic_warnings=response.warnings,
            deterministic_diagnosis=response.diagnosis,
            as_of=request.as_of_date,
            engine=self.engine,
        )
        deterministic_objections = evaluate_deterministic_objections(
            request=critique_request,
            baseline=baseline,
        )
        critique_result = self._review_critique(critique_request)
        effect = apply_critic_policy(
            mode=mode,
            response=response.model_copy(deep=True),
            request=critique_request,
            baseline=baseline,
            critique_result=critique_result,
            deterministic_objections=deterministic_objections,
        )
        response.critic_effect = effect

        critic_step = self._build_critic_trace_step(
            critique_result=critique_result,
            deterministic_objections=deterministic_objections,
            effect=effect,
            tool_calls=tool_calls,
            latency_ms=latency_ms + critique_result.latency_ms,
            token_usage=_merged_token_usage(token_usage, critique_result.token_usage),
        )
        synthesis_retries = 0
        additional_token_usage = dict(critique_result.token_usage)
        if mode == "shadow":
            return _CriticStepResult(
                step=critic_step,
                additional_token_usage=additional_token_usage,
            )

        if effect.decision_changed or effect.final_confidence != response.confidence:
            self._apply_downgrade_only_transition(
                response=response,
                proposed_decision=effect.final_decision,
                proposed_confidence=effect.final_confidence,
            )

        if effect.revisions_requested == 1:
            synthesis_retries = self._apply_bounded_revision(
                request=request,
                response=response,
                baseline=baseline,
                critique_result=critique_result,
                pending_synthesis=pending_synthesis,
                critic_step=critic_step,
                additional_token_usage=additional_token_usage,
                warnings=warnings,
            )
            effect.revisions_performed = synthesis_retries
            effect.final_decision = response.decision
            effect.final_confidence = response.confidence
            effect.decision_changed = effect.final_decision != effect.baseline_decision

        response.critic_effect = effect
        _update_critic_trace_output(critic_step, effect)
        return _CriticStepResult(
            step=critic_step,
            synthesis_retries=synthesis_retries,
            additional_token_usage=additional_token_usage,
        )

    def _build_critic_trace_step(
        self,
        *,
        critique_result: CritiqueResult,
        deterministic_objections: list[CritiqueObjection],
        effect,
        tool_calls: list[str],
        latency_ms: float,
        token_usage: dict[str, int],
    ) -> _PendingTraceStep:
        objections = [
            *deterministic_objections,
            *critique_result.response.objections,
        ]
        output = {
            "status": critique_result.response.status,
            "verdict": critique_result.response.verdict,
            "objection_counts_by_severity": _objection_counts_by_severity(objections),
            "baseline_decision": effect.baseline_decision,
            "final_decision": effect.final_decision,
            "accepted": critique_result.validation.accepted,
            "reason_codes": list(effect.reason_codes),
        }
        if critique_result.validation.accepted:
            return _PendingTraceStep(
                status="completed",
                tool_calls=tool_calls,
                output=output,
                latency_ms=latency_ms,
                token_usage=token_usage,
            )
        return _PendingTraceStep(
            status="degraded",
            reason="assessment_invalid",
            detail=",".join(critique_result.validation.reason_codes),
            tool_calls=tool_calls,
            output=output,
            latency_ms=latency_ms,
            token_usage=token_usage,
        )

    def _apply_bounded_revision(
        self,
        *,
        request: AnalyzeRequest,
        response: AnalyzeResponse,
        baseline: DraftRecommendation,
        critique_result: CritiqueResult,
        pending_synthesis: _PendingTraceStep | None,
        critic_step: _PendingTraceStep,
        additional_token_usage: dict[str, int],
        warnings: list[str],
    ) -> int:
        if MAX_CRITIC_REVISIONS != 1:
            raise ValueError("MAX_CRITIC_REVISIONS must remain 1 for the bounded critic loop.")

        revision_result = self._request_synthesis_revision(
            ticker=request.ticker,
            request=request,
            response=response,
            baseline=baseline,
            revision_instructions=critique_result.response.revision_instructions,
        )
        _merge_token_usage(additional_token_usage, revision_result.token_usage)
        if revision_result.synthesis is None:
            warnings.append("critic_revision_failed")
            if pending_synthesis is not None:
                pending_synthesis.status = "degraded"
                pending_synthesis.reason = revision_result.error_reason or "critic_revision_failed"
                pending_synthesis.detail = revision_result.error_detail
                pending_synthesis.output = {
                    **(pending_synthesis.output or {}),
                    "revision_instructions": critique_result.response.revision_instructions,
                    "revision_text": revision_result.raw_output,
                }
            critic_step.status = "degraded"
            critic_step.reason = revision_result.error_reason or "critic_revision_failed"
            critic_step.detail = revision_result.error_detail
            return 0

        try:
            validate_risk_output(revision_result.synthesis, response)
        except ValueError as exc:
            warnings.append("critic_revision_failed")
            if pending_synthesis is not None:
                pending_synthesis.status = "degraded"
                pending_synthesis.reason = "critic_revision_failed"
                pending_synthesis.detail = str(exc)
            critic_step.status = "degraded"
            critic_step.reason = "critic_revision_failed"
            critic_step.detail = str(exc)
            return 0

        self._apply_downgrade_only_transition(
            response=response,
            proposed_decision=revision_result.synthesis.decision,
            proposed_confidence=revision_result.synthesis.confidence,
        )
        if pending_synthesis is not None:
            pending_synthesis.output = {
                **(pending_synthesis.output or {}),
                "revision_guardrail": "applied",
                "revision_text": revision_result.raw_output,
            }
            pending_synthesis.latency_ms += revision_result.latency_ms
            pending_synthesis.token_usage = _merged_token_usage(
                pending_synthesis.token_usage,
                revision_result.token_usage,
            )
        return 1

    def _review_critique(self, critique_request) -> CritiqueResult:
        return Critic().review(critique_request)

    def _request_synthesis_revision(
        self,
        *,
        ticker: str,
        request: AnalyzeRequest,
        response: AnalyzeResponse,
        baseline: DraftRecommendation,
        revision_instructions: str,
    ) -> _DecisionRevisionResult:
        del ticker
        prompt = self._build_revision_prompt(
            request=request,
            response=response,
            baseline=baseline,
            revision_instructions=revision_instructions,
        )
        settings = get_settings()
        model = _GeminiDecisionRevisionModel(
            model_name=settings.GEMINI_MODEL,
            vertex_project=settings.VERTEX_AI_PROJECT,
            vertex_location=settings.VERTEX_AI_LOCATION,
            temperature=settings.AGENT_MODEL_TEMPERATURE,
        )
        started_at = time.monotonic()
        try:
            model_output = model.generate(prompt)
            synthesis = self._parse_synthesis(model_output.text)
        except RetryableModelError as exc:
            return _DecisionRevisionResult(
                synthesis=None,
                raw_output="",
                token_usage={},
                latency_ms=(time.monotonic() - started_at) * 1000,
                error_reason="critic_revision_failed",
                error_detail=str(exc),
            )

        if synthesis is None:
            return _DecisionRevisionResult(
                synthesis=None,
                raw_output=model_output.text,
                token_usage=model_output.token_usage,
                latency_ms=(time.monotonic() - started_at) * 1000,
                error_reason="critic_revision_failed",
                error_detail="unparsable_synthesis",
            )
        return _DecisionRevisionResult(
            synthesis=synthesis,
            raw_output=model_output.text,
            token_usage=model_output.token_usage,
            latency_ms=(time.monotonic() - started_at) * 1000,
        )

    def _build_revision_prompt(
        self,
        *,
        request: AnalyzeRequest,
        response: AnalyzeResponse,
        baseline: DraftRecommendation,
        revision_instructions: str,
    ) -> str:
        payload = {
            "ticker": request.ticker,
            "as_of_date": request.as_of_date.isoformat() if request.as_of_date is not None else None,
            "baseline": baseline.model_dump(mode="json"),
            "current_draft": build_draft_from_response(
                response,
                source="decision_synthesizer",
            ).model_dump(mode="json"),
            "revision_instructions": revision_instructions,
        }
        return (
            f"{HARD_RULES_TEXT}\n\n"
            "You are revising the Decision Synthesizer output for Forseti.\n"
            "Return JSON only for this schema:\n"
            "{\n"
            '  "decision": "trade|watchlist|no_trade",\n'
            '  "confidence": 0.0,\n'
            '  "reasons": [],\n'
            '  "entry_range": null,\n'
            '  "stop_loss": null,\n'
            '  "take_profit": null,\n'
            '  "risk_reward": null,\n'
            '  "position_size_eur": null\n'
            "}\n"
            "Reuse every risk number verbatim from the current draft or leave the field null.\n"
            "Never invent new money fields.\n"
            f"Revision context:\n{json.dumps(payload, sort_keys=True, indent=2)}\n"
        )

    @staticmethod
    def _synthesizer_memo(pending_synthesis: _PendingTraceStep | None) -> str:
        if pending_synthesis is None or pending_synthesis.output is None:
            return ""
        text = pending_synthesis.output.get("text")
        return text if isinstance(text, str) else ""

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

    def _apply_fundamental_policy_if_enabled(
        self,
        *,
        run_id: str,
        request: AnalyzeRequest,
        response: AnalyzeResponse,
        trace_recorder: TraceRecorder,
    ) -> FundamentalAssessmentResult | None:
        mode = _fundamental_agent_mode()
        if mode == "off":
            return None

        context_started_at = time.monotonic()
        context = build_fundamental_analysis_request(
            request.ticker,
            run_id,
            as_of=request.as_of_date,
            engine=self.engine,
        )
        trace_recorder.record_completed(
            "fundamental_context_builder",
            latency_ms=(time.monotonic() - context_started_at) * 1000,
            output={
                "context_hash": context.context_hash,
                "chunk_count": len(context.evidence_chunks),
            },
        )

        assessment_started_at = time.monotonic()
        assessment_result = FundamentalAnalyst().assess(context)
        assessment_latency_ms = (time.monotonic() - assessment_started_at) * 1000
        if assessment_result.validation.accepted:
            trace_recorder.record_completed(
                FUNDAMENTAL_ANALYST_NAME,
                latency_ms=assessment_latency_ms,
                token_usage=assessment_result.token_usage,
                output={
                    "status": assessment_result.response.status,
                    "proposed_score_adjustment": assessment_result.response.proposed_score_adjustment,
                },
            )
        else:
            trace_recorder.record_degraded(
                FUNDAMENTAL_ANALYST_NAME,
                "assessment_invalid",
                detail=",".join(assessment_result.validation.reason_codes),
                latency_ms=assessment_latency_ms,
                token_usage=assessment_result.token_usage,
                output={"status": assessment_result.response.status},
            )

        effect = apply_fundamental_policy(
            mode=mode,
            deterministic_response=response.model_copy(deep=True),
            request=context,
            assessment_result=assessment_result,
        )
        updated_effect = self._apply_policy_effect_to_response(effect, response, request)
        response.fundamental_agent_effect = updated_effect
        return assessment_result

    def _apply_policy_effect_to_response(
        self,
        effect,
        response: AnalyzeResponse,
        request: AnalyzeRequest,
    ):
        if effect.final_decision == response.decision:
            return effect
        if effect.final_decision != "trade":
            # The fundamental policy may legitimately raise the decision (e.g.
            # no_trade -> watchlist) when positive evidence is accepted in
            # enforced mode; this is existing, unchanged semantics, so it is
            # a direct assignment rather than a downgrade-only transition.
            response.decision = effect.final_decision
            return effect

        bars = get_latest_bars(
            request.ticker,
            250,
            engine=self.engine,
            as_of_date=request.as_of_date,
        )
        settings = get_settings()
        risk_config = RiskConfig(
            capital_eur=Decimal(str(settings.ACCOUNT_CAPITAL_EUR)),
            risk_per_trade_pct=Decimal(str(settings.RISK_PER_TRADE_PCT)),
        )
        risk_result = calculate_risk_levels(bars, risk_config)
        if risk_result is None or isinstance(risk_result, RiskDowngrade):
            effect.final_decision = "watchlist"
            effect.decision_changed = effect.final_decision != effect.baseline_decision
            effect.reason_codes.append("risk_gate_rejected_promotion")
            return effect

        response.decision = "trade"
        response.entry_range = (float(risk_result.entry_low), float(risk_result.entry_high))
        response.stop_loss = float(risk_result.stop_loss)
        response.take_profit = (
            float(risk_result.take_profit_1),
            float(risk_result.take_profit_2),
        )
        response.risk_reward = float(risk_result.risk_reward)
        response.position_size_eur = float(risk_result.position_size_eur)
        response.time_stop_at = calculate_time_stop_at(request.as_of_date or date.today())
        return effect

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
            fundamental_agent_effect=response.fundamental_agent_effect,
            critic_effect=response.critic_effect,
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
            fundamental_agent_effect=(
                trace.fundamental_agent_effect.model_dump(mode="json")
                if trace.fundamental_agent_effect is not None
                else None
            ),
            critic_effect=(
                trace.critic_effect.model_dump(mode="json")
                if trace.critic_effect is not None
                else None
            ),
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
        fundamental_agent_effect=run.fundamental_agent_effect,
        critic_effect=run.critic_effect,
    )
