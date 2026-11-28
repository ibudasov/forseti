"""Runnable ADK workflow with deterministic risk and decision guardrails."""
from __future__ import annotations

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import time
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, time as dt_time, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Literal, Mapping, Optional, Sequence, cast
from uuid import uuid4

from pydantic import ValidationError

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
from agents.orchestration.blackboard import (
    MAX_CRITIC_REVISIONS,
    BlackboardRuntime,
    BoundedRevisionOutcome,
    DecisionSynthesis,
    _DecisionRevisionResult,
    _FundamentalAssessmentOutcome,
    _TechnicalAssessmentOutcome,
    apply_technical_effect_to_response,
    enforce_downgrade_only,
    read_state_model,
    run_bounded_critic_revision,
    validate_risk_output,
    write_state_model,
)
from agents.orchestration.constants import (
    ANALYSIS_RESPONSE_KEY,
    CRITIC_REVISION_COUNT_KEY,
    CRITIC_STEP_META_KEY,
    CROSS_DOMAIN_TENSION_KEY,
    CROSS_DOMAIN_TENSION_NAME,
    DECISION_MEMO_RESULT_KEY,
    DECISION_SYNTHESIS_REQUEST_KEY,
    DETERMINISTIC_RESPONSE_KEY,
    EFFECTIVE_AS_OF_KEY,
    FUNDAMENTAL_ASSESSMENT_KEY,
    FUNDAMENTAL_ASSESSMENT_LATENCY_MS_KEY,
    FUNDAMENTAL_CONTEXT_LATENCY_MS_KEY,
    FUNDAMENTAL_REQUEST_KEY,
    RUN_ID_KEY,
    SPECIALIST_POLICY_FOLD_NAME,
    TECHNICAL_ASSESSMENT_KEY,
    TECHNICAL_ASSESSMENT_LATENCY_MS_KEY,
    TECHNICAL_CONTEXT_LATENCY_MS_KEY,
    TECHNICAL_REQUEST_KEY,
    TENSION_LATENCY_MS_KEY,
    TICKER_KEY,
    WORKFLOW_WARNINGS_KEY,
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
from app.rag.embedding import EmbeddingClient, embedding_usage_of
from app.schemas.analyze import AnalysisTrace, AnalyzeRequest, AnalyzeResponse, TraceStep
from app.schemas.critique import (
    CritiqueObjection,
    CritiqueRequest,
    CritiqueResult,
    CritiqueRevisionTarget,
    DraftRecommendation,
)
from app.schemas.fundamentals import (
    EvidenceChunkInput,
    FundamentalAnalysisRequest,
    FundamentalAssessmentResult,
)
from app.schemas.memo import DecisionMemo, SynthesizerEffect
from app.schemas.synthesis import DecisionMemoResult, DecisionSynthesisRequest
from app.schemas.technicals import TechnicalAgentEffect, TechnicalAnalysisRequest, TechnicalAssessmentResult
from app.schemas.tension import CrossDomainTension
from app.services.critic import Critic, RetryableModelError
from app.services.critic_policy import apply_critic_policy
from app.services.critic_rules import evaluate_deterministic_objections
from app.services.cross_domain_tension import CrossDomainTensionResolution, resolve_cross_domain_tension
from app.services.trace_cost import price_trace
from app.services.decision_synthesizer import DecisionSynthesizer, build_decision_memo, memo_hash
from app.services.critique_context import (
    build_analyst_views,
    build_critique_request,
    build_draft_from_response,
)
from app.services.fundamental_analyst import FundamentalAnalyst
from app.services.fundamental_context import build_fundamental_analysis_request
from app.services.fundamental_policy import apply_fundamental_policy
from app.services.synthesis_context import build_synthesis_request
from app.services.technical_analyst import TechnicalAnalyst
from app.services.technical_context import build_technical_analysis_request
from app.services.technical_policy import apply_technical_policy
from app.services.analyzer import analyze_request
from app.services.agent_run_dates import resolve_agent_run_as_of_date
from app.settings import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "AgenticAnalysisWorkflow",
    "DecisionSynthesis",
    "GoogleWorkflowError",
    "MAX_CRITIC_REVISIONS",
    "RunnerFactory",
    "UnresolvableTickerError",
    "enforce_downgrade_only",
    "load_trace",
    "validate_risk_output",
]

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


@dataclass
class _TypedSynthesisRun:
    """Typed Decision Synthesizer outcome for one workflow run, including its bounded revision."""

    request: DecisionSynthesisRequest
    results: list[DecisionMemoResult]

    @property
    def result(self) -> DecisionMemoResult:
        return self.results[-1]

    def record_revision(self, request: DecisionSynthesisRequest, result: DecisionMemoResult) -> None:
        self.request = request
        self.results.append(result)

    def memo(self) -> DecisionMemo:
        return build_decision_memo(self.result.response, self.request)

    def memo_claims(self) -> list[str]:
        response = self.result.response
        items = [*response.key_reasons, *response.key_risks, *response.what_would_change]
        return [response.thesis, *(item.claim for item in items)]

    def effect(self, final_decision: Literal["trade", "watchlist", "no_trade"]) -> SynthesizerEffect:
        validation_failures = [code for result in self.results for code in result.validation.reason_codes]
        return SynthesizerEffect(
            contract="typed",
            prompt_version=self.result.prompt_version,
            fallback_used=any(result.used_fallback for result in self.results),
            proposed_decision=self.results[0].response.proposed_decision,
            final_decision=final_decision,
            validation_failures=list(dict.fromkeys(validation_failures)),
            memo_hash=memo_hash(self.memo()),
        )


@dataclass(frozen=True)
class _CriticStepResult:
    step: _PendingTraceStep
    synthesis_retries: int = 0
    additional_token_usage: dict[str, int] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class _ModelOutput:
    text: str
    token_usage: dict[str, int]


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
            "cached_content_token_count",
        )
        if (value := getattr(usage_metadata, key, None)) is not None
    }


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


RunnerFactory = Callable[[AgentRegistry, str], Iterable[Any]]


def _synthesis_token_usage(typed_synthesis: "_TypedSynthesisRun | None") -> dict[str, int]:
    token_usage: dict[str, int] = {}
    if typed_synthesis is not None:
        _merge_token_usage(token_usage, typed_synthesis.result.token_usage)
    return token_usage


def _merge_token_usage(total: dict[str, int], event_usage: dict[str, int]) -> None:
    for field_name, value in event_usage.items():
        total[field_name] = total.get(field_name, 0) + value


def _nonzero_token_usage(token_usage: dict[str, int]) -> dict[str, int]:
    return {field_name: value for field_name, value in token_usage.items() if value}


def _merge_adk_event_token_usage(
    total: dict[str, int],
    *,
    author: str,
    event_usage: dict[str, int],
) -> None:
    if author == CRITIC_NAME and _critic_mode() == "off":
        nonzero_usage = _nonzero_token_usage(event_usage)
        if nonzero_usage:
            _merge_token_usage(total, nonzero_usage)
        return
    _merge_token_usage(total, event_usage)


def _skipped_critic_step(latency_ms: float, adk_token_usage: dict[str, int]) -> _CriticStepResult:
    nonzero_adk_usage = _nonzero_token_usage(adk_token_usage)
    output: dict[str, Any] = {"reason": "critic_mode_off"}
    if nonzero_adk_usage:
        output["adk_token_usage"] = nonzero_adk_usage
    return _CriticStepResult(
        step=_PendingTraceStep(
            status="skipped",
            reason="critic_mode_off",
            tool_calls=[],
            output=output,
            latency_ms=latency_ms,
            token_usage=nonzero_adk_usage,
        )
    )


def _merged_token_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    merged = dict(left)
    _merge_token_usage(merged, right)
    return merged


def _typed_analyst_trace_output(text: str) -> dict[str, Any]:
    """Build a trace payload for a typed analyst view.

    The typed-view agent emits the rendered assessment as event text; this
    re-parses it so the trace records its source alongside the rendered fields.
    """
    try:
        rendered = json.loads(text) if text else {}
    except (TypeError, ValueError):
        rendered = {"text": text}
    return {"source": "typed_assessment", **rendered}


def _objection_counts_by_severity(objections: Iterable[CritiqueObjection]) -> dict[str, int]:
    return dict(Counter(objection.severity for objection in objections))


def _critic_findings(critique_result: CritiqueResult) -> list[str]:
    response = critique_result.response
    findings = [response.revision_instructions, *(objection.claim for objection in response.objections)]
    return [finding for finding in findings if finding.strip()]


def _serialize_critic_objections(objections: Iterable[CritiqueObjection]) -> list[dict[str, str]]:
    serialized: list[dict[str, str]] = []
    for objection in objections:
        references = []
        if objection.metric_ids:
            references.append(f"metrics={','.join(objection.metric_ids)}")
        if objection.chunk_ids:
            references.append(f"chunks={','.join(str(chunk_id) for chunk_id in objection.chunk_ids)}")
        if objection.warning_codes:
            references.append(f"warnings={','.join(objection.warning_codes)}")
        reference_suffix = f" ({'; '.join(references)})" if references else ""
        serialized.append(
            {
                "objection_code": objection.objection_id,
                "category": objection.category,
                "severity": objection.severity,
                "source": objection.source,
                "reason": f"{objection.category}: {objection.claim}{reference_suffix}"[:240],
            }
        )
    return serialized


def _update_critic_trace_output(step: _PendingTraceStep, effect: Any) -> None:
    if step.output is None:
        return
    step.output["baseline_decision"] = effect.baseline_decision
    step.output["final_decision"] = effect.final_decision
    step.output["reason_codes"] = list(effect.reason_codes)


def _apply_technical_effect_to_response(effect: TechnicalAgentEffect, response: AnalyzeResponse) -> None:
    apply_technical_effect_to_response(effect, response)


def _analysis_date(request: AnalyzeRequest) -> date | None:
    return request.as_of_date


def _fundamental_agent_mode() -> Literal["off", "shadow", "enforced"]:
    mode = get_settings().FUNDAMENTAL_AGENT_MODE.strip().lower()
    if mode not in {"off", "shadow", "enforced"}:
        raise ValueError("FUNDAMENTAL_AGENT_MODE must be one of ('off', 'shadow', 'enforced').")
    return cast(Literal["off", "shadow", "enforced"], mode)


def _technical_agent_mode() -> Literal["off", "shadow", "enforced"]:
    mode = get_settings().TECHNICAL_AGENT_MODE.strip().lower()
    if mode not in {"off", "shadow", "enforced"}:
        raise ValueError("TECHNICAL_AGENT_MODE must be one of ('off', 'shadow', 'enforced').")
    return cast(Literal["off", "shadow", "enforced"], mode)


def _critic_mode() -> Literal["off", "shadow", "enforced"]:
    mode = get_settings().CRITIC_MODE.strip().lower()
    if mode not in {"off", "shadow", "enforced"}:
        raise ValueError("CRITIC_MODE must be one of ('off', 'shadow', 'enforced').")
    return cast(Literal["off", "shadow", "enforced"], mode)


def _synthesizer_contract() -> Literal["legacy", "typed"]:
    contract = get_settings().SYNTHESIZER_CONTRACT.strip().lower()
    if contract not in {"legacy", "typed"}:
        raise ValueError("SYNTHESIZER_CONTRACT must be one of ('legacy', 'typed').")
    return cast(Literal["legacy", "typed"], contract)


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


def _resolve_snapshot_at(
    *,
    effective_as_of: date,
    fundamental_outcome: _FundamentalAssessmentOutcome | None,
    technical_outcome: _TechnicalAssessmentOutcome | None,
) -> datetime:
    if fundamental_outcome is not None:
        return fundamental_outcome.context.snapshot_at
    if technical_outcome is not None:
        return technical_outcome.context.snapshot_at
    return datetime.combine(effective_as_of, dt_time.max, tzinfo=timezone.utc)


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
        decision_synthesizer: Optional[DecisionSynthesizer] = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.runner_factory = runner_factory or self._default_runner_factory
        self.llm_io_recorder = llm_io_recorder
        self.embedding_client = embedding_client
        self.decision_synthesizer = decision_synthesizer
        # Trust-tooling hook (TA-7): exposes the raw in-process TA-3
        # assessment response/request from the most recent `analyze()` call
        # so offline trust scripts (`scripts/trust_e2e.py`) can re-run the
        # numeric-claim grounding check without widening the public
        # `AnalyzeResponse` contract. `None` whenever TECHNICAL_AGENT_MODE
        # is `off` or the assessment step was never reached.
        self.last_technical_assessment: Optional[TechnicalAssessmentResult] = None
        self.last_technical_request: Optional[TechnicalAnalysisRequest] = None
        self._embedding_characters_at_start = 0

    def analyze(self, ticker_reference: str, request: Optional[AnalyzeRequest] = None) -> AnalyzeResponse:
        run_id = str(uuid4())
        self.last_technical_assessment = None
        self.last_technical_request = None
        self._embedding_characters_at_start = self._embedding_characters()
        recorder = self.llm_io_recorder or build_llm_io_recorder(run_id, config=self.config)
        trace_recorder = TraceRecorder()
        warnings: list[str] = []
        started_at = time.monotonic()

        resolved_ticker = self._resolve_ticker(ticker_reference, trace_recorder)
        request = request or AnalyzeRequest(ticker=resolved_ticker)
        analysis_date = _analysis_date(request)
        effective_as_of = request.as_of_date or datetime.now(timezone.utc).date()
        response = self._run_deterministic_pipeline(request, trace_recorder)
        deterministic_snapshot = response.model_copy(deep=True)

        fundamental_mode = _fundamental_agent_mode()
        technical_mode = _technical_agent_mode()
        synthesizer_contract = _synthesizer_contract()
        critic_mode = _critic_mode()
        if self._can_run_in_adk_blackboard(
            fundamental_mode=fundamental_mode,
            technical_mode=technical_mode,
            synthesizer_contract=synthesizer_contract,
            critic_mode=critic_mode,
        ):
            runtime = self._build_blackboard_runtime(
                fundamental_mode=fundamental_mode,
                technical_mode=technical_mode,
                synthesizer_contract=synthesizer_contract,
                critic_mode=critic_mode,
            )
            registry = self._build_registry(
                trace_recorder,
                warnings,
                today=analysis_date,
                fundamental_mode=fundamental_mode,
                technical_mode=technical_mode,
                synthesizer_contract=synthesizer_contract,
                critic_mode=critic_mode,
                blackboard_runtime=runtime,
                defer_retriever_skip=True,
            )
            if registry.root_agent is not None and SPECIALIST_POLICY_FOLD_NAME in registry.specialists:
                return self._execute_blackboard_run(
                    registry=registry,
                    resolved_ticker=resolved_ticker,
                    request=request,
                    run_id=run_id,
                    analysis_date=analysis_date,
                    effective_as_of=effective_as_of,
                    deterministic_snapshot=deterministic_snapshot,
                    response=response,
                    trace_recorder=trace_recorder,
                    warnings=warnings,
                    recorder=recorder,
                    started_at=started_at,
                )
            if RETRIEVER_TOOL_NAME not in registry.tools:
                trace_recorder.record_skipped(RETRIEVER_TOOL_NAME, "retriever_tool_unavailable")

        fundamental_outcome, technical_outcome = self._assess_specialists(
            run_id=run_id,
            request=request,
            as_of=effective_as_of,
        )
        fundamental_assessment = self._fold_fundamental_outcome(
            outcome=fundamental_outcome,
            response=response,
            trace_recorder=trace_recorder,
        )
        defer_technical_trace = fundamental_outcome is None and technical_outcome is not None
        technical_assessment = self._fold_technical_outcome(
            outcome=technical_outcome,
            deterministic_response=deterministic_snapshot,
            response=response,
            trace_recorder=trace_recorder,
            record_trace=not defer_technical_trace,
        )
        deferred_technical = (
            (technical_outcome, deterministic_snapshot, response)
            if defer_technical_trace and technical_outcome is not None
            else None
        )
        snapshot_at = _resolve_snapshot_at(
            effective_as_of=effective_as_of,
            fundamental_outcome=fundamental_outcome,
            technical_outcome=technical_outcome,
        )
        tension_resolution = self._resolve_cross_domain_tension(
            ticker=resolved_ticker,
            snapshot_at=snapshot_at,
            fundamental_outcome=fundamental_outcome,
            technical_outcome=technical_outcome,
            fundamental_assessment=fundamental_assessment,
            technical_assessment=technical_assessment,
            trace_recorder=trace_recorder,
        )
        typed_synthesis = self._synthesize_typed_memo_if_enabled(
            run_id=run_id,
            response=response,
            fundamental_assessment=fundamental_assessment,
            technical_assessment=technical_assessment,
            as_of_date=effective_as_of,
            snapshot_at=snapshot_at,
            cross_domain_tension=tension_resolution.tension,
            supplemental_evidence_chunks=tension_resolution.supplemental_chunks,
        )
        registry = self._build_registry(
            trace_recorder,
            warnings,
            today=analysis_date,
            fundamental_assessment=fundamental_assessment,
            technical_assessment=technical_assessment,
            synthesized_memo=typed_synthesis.result.rendered_memo if typed_synthesis is not None else None,
            critic_mode=critic_mode,
        )

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
                technical_assessment=technical_assessment,
                typed_synthesis=typed_synthesis,
                effective_as_of=effective_as_of,
                snapshot_at=snapshot_at,
                fundamental_request=fundamental_outcome.context if fundamental_outcome is not None else None,
                cross_domain_tension=tension_resolution.tension,
                supplemental_evidence_chunks=tension_resolution.supplemental_chunks,
                deferred_technical=deferred_technical,
                recorder=recorder,
            )
        except _AdkExecutionError as exc:
            self._record_unreached_specialists(
                trace_recorder,
                exc.result.observed_agents,
                deferred_technical=deferred_technical,
            )
            return self._handle_adk_execution_error(
                exc=exc,
                run_id=run_id,
                resolved_ticker=resolved_ticker,
                analysis_date=analysis_date,
                trace_recorder=trace_recorder,
                response=response,
                warnings=warnings,
                started_at=started_at,
                typed_synthesis=typed_synthesis,
                recorder=recorder,
            )

        self._record_unreached_specialists(
            trace_recorder,
            adk_result.observed_agents,
            deferred_technical=deferred_technical,
        )
        return self._finalize_analysis_response(
            run_id=run_id,
            resolved_ticker=resolved_ticker,
            analysis_date=analysis_date,
            trace_recorder=trace_recorder,
            response=response,
            warnings=warnings,
            adk_result=adk_result,
            started_at=started_at,
            typed_synthesis=typed_synthesis,
            recorder=recorder,
        )

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

    def _can_run_in_adk_blackboard(
        self,
        *,
        fundamental_mode: Literal["off", "shadow", "enforced"],
        technical_mode: Literal["off", "shadow", "enforced"],
        synthesizer_contract: Literal["legacy", "typed"],
        critic_mode: Literal["off", "shadow", "enforced"],
    ) -> bool:
        if _runner_ignores_registry(self.runner_factory):
            return False
        uses_registry = self.runner_factory == self._default_runner_factory or bool(
            getattr(self.runner_factory, "executes_registry", False)
        )
        if not uses_registry:
            return False
        return (
            fundamental_mode != "off"
            or technical_mode != "off"
            or synthesizer_contract == "typed"
            or critic_mode != "off"
        )

    def _build_blackboard_runtime(
        self,
        *,
        fundamental_mode: Literal["off", "shadow", "enforced"],
        technical_mode: Literal["off", "shadow", "enforced"],
        synthesizer_contract: Literal["legacy", "typed"],
        critic_mode: Literal["off", "shadow", "enforced"],
    ) -> BlackboardRuntime:
        return BlackboardRuntime(
            engine=self.engine,
            embedding_client=self.embedding_client,
            embedding_client_provider=lambda: self.embedding_client,
            fundamental_mode=fundamental_mode,
            technical_mode=technical_mode,
            synthesizer_contract=synthesizer_contract,
            critic_mode=critic_mode,
            config=self.config,
            decision_synthesizer_provider=self._typed_synthesizer,
            analyze_request_fn=lambda req, engine=None: analyze_request(req, engine=engine),
            build_fundamental_request_fn=lambda *args, **kwargs: build_fundamental_analysis_request(*args, **kwargs),
            fundamental_analyst_factory=lambda: FundamentalAnalyst(),
            build_technical_request_fn=lambda *args, **kwargs: build_technical_analysis_request(*args, **kwargs),
            technical_analyst_factory=lambda: TechnicalAnalyst(),
            resolve_tension_fn=lambda **kwargs: resolve_cross_domain_tension(**kwargs),
            build_critique_request_fn=lambda *args, **kwargs: build_critique_request(*args, **kwargs),
            evaluate_deterministic_objections_fn=lambda **kwargs: evaluate_deterministic_objections(**kwargs),
            review_critique_fn=self._review_critique,
            request_synthesis_revision_fn=lambda **kwargs: self._request_synthesis_revision(**kwargs),
            validate_risk_output_fn=lambda proposal, resp: validate_risk_output(proposal, resp),
            fundamental_mode_fn=_fundamental_agent_mode,
            technical_mode_fn=_technical_agent_mode,
            synthesizer_contract_fn=_synthesizer_contract,
            critic_mode_fn=_critic_mode,
        )

    def _build_registry(
        self,
        trace_recorder: TraceRecorder,
        warnings: list[str],
        *,
        today: date | None,
        fundamental_assessment: FundamentalAssessmentResult | None = None,
        technical_assessment: TechnicalAssessmentResult | None = None,
        synthesized_memo: str | None = None,
        critic_mode: Literal["off", "shadow", "enforced"] | None = None,
        fundamental_mode: Literal["off", "shadow", "enforced"] | None = None,
        technical_mode: Literal["off", "shadow", "enforced"] | None = None,
        synthesizer_contract: Literal["legacy", "typed"] | None = None,
        blackboard_runtime: BlackboardRuntime | None = None,
        defer_retriever_skip: bool = False,
    ) -> AgentRegistry:
        optional_kwargs = {
            "embedding_client": self.embedding_client,
            "fundamental_assessment": fundamental_assessment,
            "technical_assessment": technical_assessment,
            "synthesized_memo": synthesized_memo,
            "critic_mode": critic_mode,
            "fundamental_mode": fundamental_mode,
            "technical_mode": technical_mode,
            "synthesizer_contract": synthesizer_contract,
            "blackboard_runtime": blackboard_runtime,
        }
        registry_kwargs: dict[str, Any] = {
            "config": self.config,
            "engine": self.engine,
            "today": today,
            **{key: value for key, value in optional_kwargs.items() if value is not None},
        }
        registry = build_agent_registry(**registry_kwargs)
        if _runner_ignores_registry(self.runner_factory):
            return registry
        if RETRIEVER_TOOL_NAME not in registry.tools:
            if not defer_retriever_skip:
                trace_recorder.record_skipped(RETRIEVER_TOOL_NAME, "retriever_tool_unavailable")
            warnings.append("retriever_tool_unavailable")
        return registry

    def _execute_blackboard_run(
        self,
        *,
        registry: AgentRegistry,
        resolved_ticker: str,
        request: AnalyzeRequest,
        run_id: str,
        analysis_date: date | None,
        effective_as_of: date,
        deterministic_snapshot: AnalyzeResponse,
        response: AnalyzeResponse,
        trace_recorder: TraceRecorder,
        warnings: list[str],
        recorder: Any,
        started_at: float,
    ) -> AnalyzeResponse:
        del request
        self._record_run_metadata(recorder, run_id, resolved_ticker, registry, today=analysis_date)
        recorder.record_user_message(f"Analyze ticker {resolved_ticker}")
        initial_state: dict[str, Any] = {
            TICKER_KEY: resolved_ticker,
            RUN_ID_KEY: run_id,
            EFFECTIVE_AS_OF_KEY: effective_as_of.isoformat(),
            DETERMINISTIC_RESPONSE_KEY: write_state_model(deterministic_snapshot),
            ANALYSIS_RESPONSE_KEY: write_state_model(response),
            CRITIC_REVISION_COUNT_KEY: 0,
            WORKFLOW_WARNINGS_KEY: list(warnings),
        }
        retriever_skip_pending = (
            not _runner_ignores_registry(self.runner_factory)
            and RETRIEVER_TOOL_NAME not in registry.tools
        )
        state = dict(initial_state)
        try:
            adk_result, state, typed_synthesis = self._run_adk_blackboard(
                registry=registry,
                ticker=resolved_ticker,
                initial_state=initial_state,
                trace_recorder=trace_recorder,
                response=response,
                recorder=recorder,
                retriever_skip_pending=retriever_skip_pending,
            )
        except _AdkExecutionError as exc:
            self._sync_blackboard_technical_hooks(state)
            self._record_unreached_specialists(trace_recorder, exc.result.observed_agents)
            return self._handle_adk_execution_error(
                exc=exc,
                run_id=run_id,
                resolved_ticker=resolved_ticker,
                analysis_date=analysis_date,
                trace_recorder=trace_recorder,
                response=response,
                warnings=warnings,
                started_at=started_at,
                typed_synthesis=None,
                recorder=recorder,
            )

        self._sync_blackboard_response_and_hooks(response=response, state=state)
        self._record_unreached_specialists(trace_recorder, adk_result.observed_agents)
        return self._finalize_analysis_response(
            run_id=run_id,
            resolved_ticker=resolved_ticker,
            analysis_date=analysis_date,
            trace_recorder=trace_recorder,
            response=response,
            warnings=warnings,
            adk_result=adk_result,
            started_at=started_at,
            typed_synthesis=typed_synthesis,
            recorder=recorder,
        )

    def _run_adk_blackboard(
        self,
        *,
        registry: AgentRegistry,
        ticker: str,
        initial_state: dict[str, Any],
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        recorder: Any,
        retriever_skip_pending: bool = False,
    ) -> tuple[_AdkRunResult, dict[str, Any], _TypedSynthesisRun | None]:
        state = dict(initial_state)
        token_usage: dict[str, int] = {}
        warnings: list[str] = []
        observed_agents: list[str] = []
        event_count = 0
        previous_event_at = time.monotonic()
        pending_synthesis: _PendingTraceStep | None = None
        initial_memo_result: DecisionMemoResult | None = None
        buffered_specialists: dict[str, dict[str, Any]] = {}
        pending_flags = {"retriever_skip": retriever_skip_pending}

        try:
            events, final_state = self._run_blackboard_events(registry, ticker, initial_state)
            for event in events:
                event_count += 1
                if recorder is not None:
                    recorder.record_event(event_count - 1, event)
                self._apply_event_state_delta(state, event)
                now = time.monotonic()
                latency_ms = (now - previous_event_at) * 1000
                previous_event_at = now
                pending_synthesis, initial_memo_result = self._dispatch_blackboard_event(
                    event=event,
                    state=state,
                    buffered_specialists=buffered_specialists,
                    trace_recorder=trace_recorder,
                    observed_agents=observed_agents,
                    response=response,
                    warnings=warnings,
                    token_usage=token_usage,
                    latency_ms=latency_ms,
                    pending_synthesis=pending_synthesis,
                    initial_memo_result=initial_memo_result,
                    pending_flags=pending_flags,
                )

            if final_state:
                state.update(final_state)
            if pending_flags["retriever_skip"]:
                trace_recorder.record_skipped(RETRIEVER_TOOL_NAME, "retriever_tool_unavailable")
                pending_flags["retriever_skip"] = False
            self._flush_pending_synthesis(trace_recorder, pending_synthesis)
            typed_synthesis = self._build_blackboard_typed_synthesis(
                state=state,
                initial_memo_result=initial_memo_result,
            )
            return (
                _AdkRunResult(
                    token_usage=token_usage,
                    warnings=warnings,
                    event_count=event_count,
                    observed_agents=observed_agents,
                ),
                state,
                typed_synthesis,
            )
        except Exception as exc:
            if pending_flags["retriever_skip"]:
                trace_recorder.record_skipped(RETRIEVER_TOOL_NAME, "retriever_tool_unavailable")
                pending_flags["retriever_skip"] = False
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

    def _dispatch_blackboard_event(
        self,
        *,
        event: Any,
        state: dict[str, Any],
        buffered_specialists: dict[str, dict[str, Any]],
        trace_recorder: TraceRecorder,
        observed_agents: list[str],
        response: AnalyzeResponse,
        warnings: list[str],
        token_usage: dict[str, int],
        latency_ms: float,
        pending_synthesis: _PendingTraceStep | None,
        initial_memo_result: DecisionMemoResult | None,
        pending_flags: dict[str, bool],
    ) -> tuple[_PendingTraceStep | None, DecisionMemoResult | None]:
        author = event_author(event)
        tool_calls = event_function_calls(event)
        text = event_text(event)
        usage = event_token_usage(event)
        error_detail = event_error(event)

        if author in {FUNDAMENTAL_ANALYST_NAME, TECHNICAL_ANALYST_NAME}:
            buffered_specialists[author] = {
                "tool_calls": tool_calls,
                "text": text,
                "usage": usage,
                "latency_ms": latency_ms,
                "error_detail": error_detail,
            }
            if error_detail is not None:
                warnings.append(f"agent_narration_degraded: {error_detail}")
            return pending_synthesis, initial_memo_result

        if author == SPECIALIST_POLICY_FOLD_NAME:
            self._flush_blackboard_specialists(
                state=state,
                buffered=buffered_specialists,
                trace_recorder=trace_recorder,
                observed_agents=observed_agents,
                token_usage=token_usage,
            )
            return pending_synthesis, initial_memo_result

        if author == CROSS_DOMAIN_TENSION_NAME:
            self._handle_blackboard_tension_event(
                state=state,
                latency_ms=latency_ms,
                trace_recorder=trace_recorder,
                observed_agents=observed_agents,
            )
            if pending_flags["retriever_skip"]:
                trace_recorder.record_skipped(RETRIEVER_TOOL_NAME, "retriever_tool_unavailable")
                pending_flags["retriever_skip"] = False
            return pending_synthesis, initial_memo_result

        if pending_flags["retriever_skip"]:
            trace_recorder.record_skipped(RETRIEVER_TOOL_NAME, "retriever_tool_unavailable")
            pending_flags["retriever_skip"] = False

        if author not in observed_agents:
            observed_agents.append(author)

        return self._dispatch_blackboard_post_tension_event(
            author=author,
            state=state,
            trace_recorder=trace_recorder,
            response=response,
            warnings=warnings,
            tool_calls=tool_calls,
            text=text,
            usage=usage,
            error_detail=error_detail,
            latency_ms=latency_ms,
            token_usage=token_usage,
            pending_synthesis=pending_synthesis,
            initial_memo_result=initial_memo_result,
        )

    def _dispatch_blackboard_post_tension_event(
        self,
        *,
        author: str,
        state: dict[str, Any],
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        warnings: list[str],
        tool_calls: list[str],
        text: str,
        usage: dict[str, int],
        error_detail: str | None,
        latency_ms: float,
        token_usage: dict[str, int],
        pending_synthesis: _PendingTraceStep | None,
        initial_memo_result: DecisionMemoResult | None,
    ) -> tuple[_PendingTraceStep | None, DecisionMemoResult | None]:
        if error_detail is not None:
            self._flush_pending_synthesis(trace_recorder, pending_synthesis)
            _merge_adk_event_token_usage(token_usage, author=author, event_usage=usage)
            warnings.append(f"agent_narration_degraded: {error_detail}")
            self._record_degraded_adk_event(
                trace_recorder=trace_recorder,
                author=author,
                error_detail=error_detail,
                tool_calls=tool_calls,
                text=text,
                latency_ms=latency_ms,
                usage=usage,
            )
            return None, initial_memo_result

        if author == DECISION_SYNTHESIZER_NAME:
            return self._handle_blackboard_synthesizer_event(
                state=state,
                response=response,
                warnings=warnings,
                text=text,
                tool_calls=tool_calls,
                latency_ms=latency_ms,
                usage=usage,
                token_usage=token_usage,
            )

        if author == CRITIC_NAME:
            self._handle_blackboard_critic_event(
                state=state,
                trace_recorder=trace_recorder,
                pending_synthesis=pending_synthesis,
                tool_calls=tool_calls,
                latency_ms=latency_ms,
                usage=usage,
                token_usage=token_usage,
            )
            return None, initial_memo_result

        self._flush_pending_synthesis(trace_recorder, pending_synthesis)
        _merge_adk_event_token_usage(token_usage, author=author, event_usage=usage)
        trace_recorder.record_completed(
            author,
            tool_calls=tool_calls,
            output={"text": text} if text else None,
            latency_ms=latency_ms,
            token_usage=usage,
        )
        return None, initial_memo_result

    def _run_blackboard_events(
        self,
        registry: AgentRegistry,
        ticker: str,
        initial_state: dict[str, Any],
    ) -> tuple[list[Any], dict[str, Any]]:
        if self.runner_factory != self._default_runner_factory:
            return list(self.runner_factory(registry, ticker)), {}

        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part

        session_service = InMemorySessionService()
        session = asyncio.run(
            session_service.create_session(
                app_name="forseti",
                user_id=ticker,
                state=dict(initial_state),
            )
        )
        runner = Runner(
            app_name="forseti",
            agent=registry.root_agent,
            session_service=session_service,
        )
        events = list(
            runner.run(
                user_id=ticker,
                session_id=session.id,
                new_message=Content(role="user", parts=[Part(text=f"Analyze ticker {ticker}")]),
            )
        )
        final_session = asyncio.run(
            session_service.get_session(
                app_name="forseti",
                user_id=ticker,
                session_id=session.id,
            )
        )
        return events, dict(final_session.state) if final_session is not None else {}

    @staticmethod
    def _apply_event_state_delta(state: dict[str, Any], event: Any) -> None:
        actions = getattr(event, "actions", None)
        state_delta = getattr(actions, "state_delta", None)
        if isinstance(state_delta, dict):
            state.update(state_delta)

    def _flush_blackboard_specialists(
        self,
        *,
        state: dict[str, Any],
        buffered: dict[str, dict[str, Any]],
        trace_recorder: TraceRecorder,
        observed_agents: list[str],
        token_usage: dict[str, int],
    ) -> None:
        fund_req = read_state_model(state, FUNDAMENTAL_REQUEST_KEY, FundamentalAnalysisRequest)
        fund_res = read_state_model(state, FUNDAMENTAL_ASSESSMENT_KEY, FundamentalAssessmentResult)
        tech_req = read_state_model(state, TECHNICAL_REQUEST_KEY, TechnicalAnalysisRequest)
        tech_res = read_state_model(state, TECHNICAL_ASSESSMENT_KEY, TechnicalAssessmentResult)

        if fund_req is not None and fund_res is not None:
            self._record_fundamental_steps_from_state(
                fund_req=fund_req,
                fund_res=fund_res,
                context_latency_ms=float(state.get(FUNDAMENTAL_CONTEXT_LATENCY_MS_KEY, 0.0)),
                assessment_latency_ms=float(state.get(FUNDAMENTAL_ASSESSMENT_LATENCY_MS_KEY, 0.0)),
                trace_recorder=trace_recorder,
            )
        else:
            self._flush_one_buffered_specialist(
                author=FUNDAMENTAL_ANALYST_NAME,
                buffered=buffered,
                is_typed=False,
                trace_recorder=trace_recorder,
                observed_agents=observed_agents,
                token_usage=token_usage,
            )

        if tech_req is not None and tech_res is not None:
            self._record_technical_steps_from_state(
                tech_req=tech_req,
                tech_res=tech_res,
                context_latency_ms=float(state.get(TECHNICAL_CONTEXT_LATENCY_MS_KEY, 0.0)),
                assessment_latency_ms=float(state.get(TECHNICAL_ASSESSMENT_LATENCY_MS_KEY, 0.0)),
                trace_recorder=trace_recorder,
            )

        self._flush_one_buffered_specialist(
            author=FUNDAMENTAL_ANALYST_NAME,
            buffered=buffered,
            is_typed=fund_res is not None,
            trace_recorder=trace_recorder,
            observed_agents=observed_agents,
            token_usage=token_usage,
        )
        self._flush_one_buffered_specialist(
            author=TECHNICAL_ANALYST_NAME,
            buffered=buffered,
            is_typed=tech_res is not None,
            trace_recorder=trace_recorder,
            observed_agents=observed_agents,
            token_usage=token_usage,
        )
        buffered.clear()
        self._sync_blackboard_technical_hooks(state)

    def _flush_one_buffered_specialist(
        self,
        *,
        author: str,
        buffered: dict[str, dict[str, Any]],
        is_typed: bool,
        trace_recorder: TraceRecorder,
        observed_agents: list[str],
        token_usage: dict[str, int],
    ) -> None:
        payload = buffered.pop(author, None)
        if payload is None:
            return
        if author not in observed_agents:
            observed_agents.append(author)
        self._record_buffered_specialist_view(
            author=author,
            payload=payload,
            is_typed=is_typed,
            trace_recorder=trace_recorder,
            token_usage=token_usage,
        )

    def _record_buffered_specialist_view(
        self,
        *,
        author: str,
        payload: dict[str, Any],
        is_typed: bool,
        trace_recorder: TraceRecorder,
        token_usage: dict[str, int],
    ) -> None:
        usage = payload["usage"]
        _merge_adk_event_token_usage(token_usage, author=author, event_usage=usage)
        if payload["error_detail"] is not None:
            self._record_degraded_adk_event(
                trace_recorder=trace_recorder,
                author=author,
                error_detail=payload["error_detail"],
                tool_calls=payload["tool_calls"],
                text=payload["text"],
                latency_ms=payload["latency_ms"],
                usage=usage,
            )
            return
        output = (
            _typed_analyst_trace_output(payload["text"])
            if is_typed
            else ({"text": payload["text"]} if payload["text"] else None)
        )
        trace_recorder.record_completed(
            author,
            tool_calls=payload["tool_calls"],
            output=output,
            latency_ms=payload["latency_ms"],
            token_usage=usage,
        )

    def _handle_blackboard_tension_event(
        self,
        *,
        state: dict[str, Any],
        latency_ms: float,
        trace_recorder: TraceRecorder,
        observed_agents: list[str],
    ) -> None:
        tension = read_state_model(state, CROSS_DOMAIN_TENSION_KEY, CrossDomainTension)
        if tension is None:
            return
        if CROSS_DOMAIN_TENSION_NAME not in observed_agents:
            observed_agents.append(CROSS_DOMAIN_TENSION_NAME)
        trace_recorder.record_completed(
            CROSS_DOMAIN_TENSION_NAME,
            latency_ms=float(state.get(TENSION_LATENCY_MS_KEY, latency_ms)),
            token_usage={},
            output={
                "conflict_type": tension.conflict_type,
                "fundamental_signal": tension.fundamental_signal,
                "technical_signal": tension.technical_signal,
                "supplemental_chunk_ids": list(tension.supplemental_chunk_ids),
                "date_window_days": tension.date_window_days,
            },
        )

    def _handle_blackboard_synthesizer_event(
        self,
        *,
        state: dict[str, Any],
        response: AnalyzeResponse,
        warnings: list[str],
        text: str,
        tool_calls: list[str],
        latency_ms: float,
        usage: dict[str, int],
        token_usage: dict[str, int],
    ) -> tuple[_PendingTraceStep, DecisionMemoResult | None]:
        _merge_adk_event_token_usage(token_usage, author=DECISION_SYNTHESIZER_NAME, event_usage=usage)
        memo_result = read_state_model(state, DECISION_MEMO_RESULT_KEY, DecisionMemoResult)
        synth_request = read_state_model(state, DECISION_SYNTHESIS_REQUEST_KEY, DecisionSynthesisRequest)
        if memo_result is None or synth_request is None:
            step = self._build_decision_synthesizer_step(
                response=response,
                warnings=warnings,
                text=text,
                tool_calls=tool_calls,
                latency_ms=latency_ms,
                token_usage=usage,
            )
            return step, None

        _merge_token_usage(token_usage, memo_result.token_usage)
        typed_run = _TypedSynthesisRun(request=synth_request, results=[memo_result])
        step = self._build_typed_synthesizer_step(
            response=response,
            typed_synthesis=typed_run,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
            token_usage=usage,
        )
        return step, memo_result

    def _handle_blackboard_critic_event(
        self,
        *,
        state: dict[str, Any],
        trace_recorder: TraceRecorder,
        pending_synthesis: _PendingTraceStep | None,
        tool_calls: list[str],
        latency_ms: float,
        usage: dict[str, int],
        token_usage: dict[str, int],
    ) -> None:
        _merge_adk_event_token_usage(token_usage, author=CRITIC_NAME, event_usage=usage)
        mode = _critic_mode()
        if mode == "off":
            critic_result = _skipped_critic_step(latency_ms, usage)
            self._flush_pending_synthesis(trace_recorder, pending_synthesis)
            self._record_pending_step(trace_recorder, CRITIC_NAME, critic_result.step)
            return None

        meta = dict(state.get(CRITIC_STEP_META_KEY) or {})
        critique_tokens = dict(meta.get("critique_token_usage") or {})
        revision_tokens = dict(meta.get("revision_token_usage") or {})
        _merge_token_usage(token_usage, critique_tokens)
        _merge_token_usage(token_usage, revision_tokens)
        revisions_performed = int(meta.get("revisions_performed", 0))
        revision_rejected = bool(meta.get("revision_rejected", False))
        self._update_pending_synthesis_from_blackboard_meta(
            pending_synthesis=pending_synthesis,
            meta=meta,
            is_typed=DECISION_MEMO_RESULT_KEY in state,
        )
        self._flush_pending_synthesis(
            trace_recorder,
            pending_synthesis,
            retries=revisions_performed,
        )

        output = dict(meta.get("output") or {})
        output["adk_token_usage"] = dict(usage)
        if revisions_performed == 1 or revision_rejected:
            output["revision_objection_ids"] = list(meta.get("revision_objection_ids") or [])
            output["revision_targets"] = list(meta.get("revision_targets") or [])

        step_tokens = _merged_token_usage(usage, critique_tokens)
        step_latency = latency_ms + float(meta.get("critique_latency_ms", 0.0))
        critique_accepted = bool(meta.get("critique_accepted", True))
        if revision_rejected:
            critic_step = _PendingTraceStep(
                status="degraded",
                reason=str(meta.get("revision_error_reason") or "critic_revision_failed"),
                detail=meta.get("revision_error_detail"),
                tool_calls=tool_calls,
                output=output,
                latency_ms=step_latency,
                token_usage=step_tokens,
            )
        elif critique_accepted:
            critic_step = _PendingTraceStep(
                status="completed",
                tool_calls=tool_calls,
                output=output,
                latency_ms=step_latency,
                token_usage=step_tokens,
            )
        else:
            critic_step = _PendingTraceStep(
                status="degraded",
                reason="assessment_invalid",
                detail="; ".join(meta.get("critique_diagnostics") or []),
                tool_calls=tool_calls,
                output=output,
                latency_ms=step_latency,
                token_usage=step_tokens,
            )
        self._record_pending_step(trace_recorder, CRITIC_NAME, critic_step)
        return None

    @staticmethod
    def _update_pending_synthesis_from_blackboard_meta(
        *,
        pending_synthesis: _PendingTraceStep | None,
        meta: dict[str, Any],
        is_typed: bool,
    ) -> None:
        if pending_synthesis is None:
            return
        revisions_performed = int(meta.get("revisions_performed", 0))
        revision_rejected = bool(meta.get("revision_rejected", False))
        if revision_rejected:
            pending_synthesis.status = "degraded"
            pending_synthesis.reason = str(meta.get("revision_error_reason") or "critic_revision_failed")
            pending_synthesis.detail = meta.get("revision_error_detail")
            pending_synthesis.output = {
                **(pending_synthesis.output or {}),
                "revision_text": meta.get("revision_raw_output", ""),
                "revision_objection_ids": list(meta.get("revision_objection_ids") or []),
                "revision_targets": list(meta.get("revision_targets") or []),
            }
            return
        if revisions_performed == 1:
            pending_synthesis.output = {
                **(pending_synthesis.output or {}),
                "revision_guardrail": "applied",
                "revision_text": meta.get("revision_raw_output", ""),
                "revision_objection_ids": list(meta.get("revision_objection_ids") or []),
                "revision_targets": list(meta.get("revision_targets") or []),
            }
            pending_synthesis.latency_ms += float(meta.get("revision_latency_ms", 0.0))
            if not is_typed:
                pending_synthesis.token_usage = _merged_token_usage(
                    pending_synthesis.token_usage,
                    dict(meta.get("revision_token_usage") or {}),
                )

    def _sync_blackboard_technical_hooks(self, state: Mapping[str, Any]) -> None:
        tech_req = read_state_model(state, TECHNICAL_REQUEST_KEY, TechnicalAnalysisRequest)
        tech_res = read_state_model(state, TECHNICAL_ASSESSMENT_KEY, TechnicalAssessmentResult)
        if tech_res is not None:
            self.last_technical_assessment = tech_res
        if tech_req is not None:
            self.last_technical_request = tech_req

    def _sync_blackboard_response_and_hooks(
        self,
        *,
        response: AnalyzeResponse,
        state: Mapping[str, Any],
    ) -> None:
        self._sync_blackboard_technical_hooks(state)
        final_resp = read_state_model(state, ANALYSIS_RESPONSE_KEY, AnalyzeResponse)
        if final_resp is None:
            return
        response.decision = final_resp.decision
        response.confidence = final_resp.confidence
        response.reasons = list(final_resp.reasons)
        response.warnings = list(final_resp.warnings)
        for field_name in (
            "entry_range",
            "stop_loss",
            "take_profit",
            "risk_reward",
            "position_size_eur",
            "time_stop_at",
            "memo",
            "fundamental_agent_effect",
            "technical_agent_effect",
            "critic_effect",
        ):
            setattr(response, field_name, getattr(final_resp, field_name))
        extra_warnings = state.get(WORKFLOW_WARNINGS_KEY)
        if isinstance(extra_warnings, list):
            for warning in extra_warnings:
                if warning not in response.warnings:
                    response.warnings.append(warning)

    @staticmethod
    def _build_blackboard_typed_synthesis(
        *,
        state: Mapping[str, Any],
        initial_memo_result: DecisionMemoResult | None,
    ) -> _TypedSynthesisRun | None:
        synth_request = read_state_model(state, DECISION_SYNTHESIS_REQUEST_KEY, DecisionSynthesisRequest)
        final_memo_result = read_state_model(state, DECISION_MEMO_RESULT_KEY, DecisionMemoResult)
        if synth_request is None or final_memo_result is None:
            return None
        results: list[DecisionMemoResult] = []
        if initial_memo_result is not None and initial_memo_result != final_memo_result:
            results.append(initial_memo_result)
        results.append(final_memo_result)
        return _TypedSynthesisRun(request=synth_request, results=results)

    @staticmethod
    def _record_degraded_adk_event(
        *,
        trace_recorder: TraceRecorder,
        author: str,
        error_detail: str,
        tool_calls: list[str],
        text: str,
        latency_ms: float,
        usage: dict[str, int],
    ) -> None:
        output: dict[str, Any] | None = {"text": text} if text else None
        if author == CRITIC_NAME:
            nonzero_adk_usage = _nonzero_token_usage(usage)
            if nonzero_adk_usage:
                output = {**(output or {}), "adk_token_usage": nonzero_adk_usage}
        trace_recorder.record_degraded(
            author,
            "agent_narration_degraded",
            detail=error_detail,
            tool_calls=tool_calls,
            output=output,
            latency_ms=latency_ms,
            token_usage=usage,
        )

    def _handle_adk_execution_error(
        self,
        *,
        exc: _AdkExecutionError,
        run_id: str,
        resolved_ticker: str,
        analysis_date: date | None,
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        warnings: list[str],
        started_at: float,
        typed_synthesis: _TypedSynthesisRun | None,
        recorder: Any,
    ) -> AnalyzeResponse:
        final_warnings = list(response.warnings) + warnings + exc.result.warnings
        response.warnings = final_warnings
        partial_trace = self._build_trace(
            run_id=run_id,
            ticker=resolved_ticker,
            as_of_date=analysis_date,
            trace_recorder=trace_recorder,
            response=response,
            token_usage=exc.result.token_usage,
            warnings=final_warnings,
            total_latency_ms=(time.monotonic() - started_at) * 1000,
            adk_event_count=exc.result.event_count,
            observed_agents=exc.result.observed_agents,
            typed_synthesis=typed_synthesis,
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

    def _finalize_analysis_response(
        self,
        *,
        run_id: str,
        resolved_ticker: str,
        analysis_date: date | None,
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        warnings: list[str],
        adk_result: _AdkRunResult,
        started_at: float,
        typed_synthesis: _TypedSynthesisRun | None,
        recorder: Any,
    ) -> AnalyzeResponse:
        final_warnings = list(dict.fromkeys([*response.warnings, *warnings, *adk_result.warnings]))
        response.warnings = final_warnings
        total_latency_ms = (time.monotonic() - started_at) * 1000
        trace = self._build_trace(
            run_id=run_id,
            ticker=resolved_ticker,
            as_of_date=analysis_date,
            trace_recorder=trace_recorder,
            response=response,
            token_usage=adk_result.token_usage,
            warnings=final_warnings,
            total_latency_ms=total_latency_ms,
            adk_event_count=adk_result.event_count,
            observed_agents=adk_result.observed_agents,
            typed_synthesis=typed_synthesis,
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
        technical_assessment: TechnicalAssessmentResult | None,
        typed_synthesis: _TypedSynthesisRun | None = None,
        effective_as_of: date | None = None,
        snapshot_at: datetime | None = None,
        fundamental_request: FundamentalAnalysisRequest | None = None,
        cross_domain_tension: CrossDomainTension | None = None,
        supplemental_evidence_chunks: Sequence[EvidenceChunkInput] = (),
        deferred_technical: tuple[_TechnicalAssessmentOutcome, AnalyzeResponse, AnalyzeResponse] | None = None,
        recorder=None,
    ) -> _AdkRunResult:
        token_usage = _synthesis_token_usage(typed_synthesis)
        warnings: list[str] = []
        observed_agents: list[str] = []
        event_count = 0
        previous_event_at = time.monotonic()
        pending_synthesis: _PendingTraceStep | None = None
        pending_deferred_technical = deferred_technical

        try:
            events = self.runner_factory(registry, ticker)
            for event in events:
                event_count += 1
                if recorder is not None:
                    recorder.record_event(event_count - 1, event)

                now = time.monotonic()
                latency_ms = (now - previous_event_at) * 1000
                previous_event_at = now
                pending_synthesis, pending_deferred_technical = self._dispatch_adk_event(
                    event=event,
                    ticker=ticker,
                    request=request,
                    run_id=run_id,
                    trace_recorder=trace_recorder,
                    response=response,
                    baseline=baseline,
                    fundamental_assessment=fundamental_assessment,
                    technical_assessment=technical_assessment,
                    typed_synthesis=typed_synthesis,
                    effective_as_of=effective_as_of,
                    snapshot_at=snapshot_at,
                    fundamental_request=fundamental_request,
                    cross_domain_tension=cross_domain_tension,
                    supplemental_evidence_chunks=supplemental_evidence_chunks,
                    warnings=warnings,
                    observed_agents=observed_agents,
                    token_usage=token_usage,
                    latency_ms=latency_ms,
                    pending_synthesis=pending_synthesis,
                    pending_deferred_technical=pending_deferred_technical,
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

    def _dispatch_adk_event(
        self,
        *,
        event: Any,
        ticker: str,
        request: AnalyzeRequest,
        run_id: str,
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        baseline: DraftRecommendation,
        fundamental_assessment: FundamentalAssessmentResult | None,
        technical_assessment: TechnicalAssessmentResult | None,
        typed_synthesis: _TypedSynthesisRun | None,
        effective_as_of: date | None,
        snapshot_at: datetime | None,
        fundamental_request: FundamentalAnalysisRequest | None,
        cross_domain_tension: CrossDomainTension | None,
        supplemental_evidence_chunks: Sequence[EvidenceChunkInput],
        warnings: list[str],
        observed_agents: list[str],
        token_usage: dict[str, int],
        latency_ms: float,
        pending_synthesis: _PendingTraceStep | None,
        pending_deferred_technical: tuple[_TechnicalAssessmentOutcome, AnalyzeResponse, AnalyzeResponse] | None,
    ) -> tuple[
        _PendingTraceStep | None,
        tuple[_TechnicalAssessmentOutcome, AnalyzeResponse, AnalyzeResponse] | None,
    ]:
        author = event_author(event)
        if author not in observed_agents:
            observed_agents.append(author)
        tool_calls = event_function_calls(event)
        text = event_text(event)
        usage = event_token_usage(event)
        _merge_adk_event_token_usage(token_usage, author=author, event_usage=usage)

        if author in {TECHNICAL_ANALYST_NAME, DECISION_SYNTHESIZER_NAME, CRITIC_NAME}:
            self._flush_deferred_technical(
                trace_recorder,
                pending_deferred_technical,
            )
            pending_deferred_technical = None

        error_detail = event_error(event)
        if error_detail is not None:
            self._flush_pending_synthesis(trace_recorder, pending_synthesis)
            warnings.append(f"agent_narration_degraded: {error_detail}")
            self._record_degraded_adk_event(
                trace_recorder=trace_recorder,
                author=author,
                error_detail=error_detail,
                tool_calls=tool_calls,
                text=text,
                latency_ms=latency_ms,
                usage=usage,
            )
            if author == FUNDAMENTAL_ANALYST_NAME:
                self._flush_deferred_technical(
                    trace_recorder,
                    pending_deferred_technical,
                )
                pending_deferred_technical = None
            return None, pending_deferred_technical

        if author == DECISION_SYNTHESIZER_NAME:
            next_pending = self._build_synthesis_step(
                response=response,
                warnings=warnings,
                typed_synthesis=typed_synthesis,
                text=text,
                tool_calls=tool_calls,
                latency_ms=latency_ms,
                token_usage=usage,
            )
            return next_pending, pending_deferred_technical

        if author == CRITIC_NAME:
            critic_result = self._record_critic_policy_step(
                ticker=ticker,
                request=request,
                run_id=run_id,
                trace_recorder=trace_recorder,
                response=response,
                baseline=baseline,
                fundamental_assessment=fundamental_assessment,
                technical_assessment=technical_assessment,
                warnings=warnings,
                tool_calls=tool_calls,
                latency_ms=latency_ms,
                token_usage=usage,
                pending_synthesis=pending_synthesis,
                typed_synthesis=typed_synthesis,
                effective_as_of=effective_as_of,
                snapshot_at=snapshot_at,
                fundamental_request=fundamental_request,
                cross_domain_tension=cross_domain_tension,
                supplemental_evidence_chunks=supplemental_evidence_chunks,
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
            return None, pending_deferred_technical

        self._flush_pending_synthesis(trace_recorder, pending_synthesis)
        is_typed_analyst = (
            (author == FUNDAMENTAL_ANALYST_NAME and fundamental_assessment is not None)
            or (author == TECHNICAL_ANALYST_NAME and technical_assessment is not None)
        )
        output = _typed_analyst_trace_output(text) if is_typed_analyst else ({"text": text} if text else None)
        trace_recorder.record_completed(
            author,
            tool_calls=tool_calls,
            output=output,
            latency_ms=latency_ms,
            token_usage=usage,
        )
        if author == FUNDAMENTAL_ANALYST_NAME:
            self._flush_deferred_technical(
                trace_recorder,
                pending_deferred_technical,
            )
            pending_deferred_technical = None
        return None, pending_deferred_technical

    def _synthesize_typed_memo_if_enabled(
        self,
        *,
        run_id: str,
        response: AnalyzeResponse,
        fundamental_assessment: FundamentalAssessmentResult | None,
        technical_assessment: TechnicalAssessmentResult | None,
        as_of_date: date,
        snapshot_at: datetime,
        cross_domain_tension: CrossDomainTension | None = None,
        supplemental_evidence_chunks: Sequence[EvidenceChunkInput] = (),
    ) -> _TypedSynthesisRun | None:
        if _synthesizer_contract() != "typed":
            return None

        synthesis_request = build_synthesis_request(
            run_id=run_id,
            response=response,
            analyst_views=build_analyst_views(
                fundamental=fundamental_assessment, technical=technical_assessment
            ),
            as_of_date=as_of_date,
            snapshot_at=snapshot_at,
            cross_domain_tension=cross_domain_tension,
            supplemental_evidence_chunks=supplemental_evidence_chunks,
        )
        result = self._typed_synthesizer().synthesize(synthesis_request)
        return _TypedSynthesisRun(request=synthesis_request, results=[result])

    def _typed_synthesizer(self) -> DecisionSynthesizer:
        if self.decision_synthesizer is None:
            self.decision_synthesizer = DecisionSynthesizer()
        return self.decision_synthesizer

    def _build_synthesis_step(
        self,
        *,
        response: AnalyzeResponse,
        warnings: list[str],
        typed_synthesis: _TypedSynthesisRun | None,
        text: str,
        tool_calls: list[str],
        latency_ms: float,
        token_usage: dict[str, int],
    ) -> _PendingTraceStep:
        if typed_synthesis is None:
            return self._build_decision_synthesizer_step(
                response=response,
                warnings=warnings,
                text=text,
                tool_calls=tool_calls,
                latency_ms=latency_ms,
                token_usage=token_usage,
            )
        return self._build_typed_synthesizer_step(
            response=response,
            typed_synthesis=typed_synthesis,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
            token_usage=token_usage,
        )

    def _build_typed_synthesizer_step(
        self,
        *,
        response: AnalyzeResponse,
        typed_synthesis: _TypedSynthesisRun,
        tool_calls: list[str],
        latency_ms: float,
        token_usage: dict[str, int],
    ) -> _PendingTraceStep:
        result = typed_synthesis.result
        was_downgraded = self._apply_downgrade_only_transition(
            response=response,
            proposed_decision=result.response.proposed_decision,
            proposed_confidence=result.response.confidence,
        )
        response.memo = typed_synthesis.memo()
        output: dict[str, Any] = {
            "source": "typed_synthesis",
            "text": result.rendered_memo,
            "guardrail": "downgraded" if was_downgraded else "unchanged",
            "prompt_version": result.prompt_version,
            "used_fallback": result.used_fallback,
        }
        step_latency_ms = latency_ms + result.latency_ms
        if not result.used_fallback:
            return _PendingTraceStep(
                status="completed",
                tool_calls=tool_calls,
                output=output,
                latency_ms=step_latency_ms,
                token_usage=token_usage,
            )
        return _PendingTraceStep(
            status="degraded",
            reason="synthesis_fallback",
            detail=",".join(result.validation.reason_codes),
            tool_calls=tool_calls,
            output=output,
            latency_ms=step_latency_ms,
            token_usage=token_usage,
        )

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
                output=step.output,
                token_usage=step.token_usage,
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
        technical_assessment: TechnicalAssessmentResult | None,
        warnings: list[str],
        tool_calls: list[str],
        latency_ms: float,
        token_usage: dict[str, int],
        pending_synthesis: _PendingTraceStep | None,
        typed_synthesis: _TypedSynthesisRun | None = None,
        effective_as_of: date | None = None,
        snapshot_at: datetime | None = None,
        fundamental_request: FundamentalAnalysisRequest | None = None,
        cross_domain_tension: CrossDomainTension | None = None,
        supplemental_evidence_chunks: Sequence[EvidenceChunkInput] = (),
    ) -> _CriticStepResult:
        # `technical_assessment` threads TA-4's validated result into the
        # Critic, so there is a single technical interpretation per run.
        del trace_recorder
        mode = _critic_mode()
        if mode == "off":
            return _skipped_critic_step(latency_ms, token_usage)

        critique_request = self._build_run_critique_request(
            ticker=ticker,
            run_id=run_id,
            response=response,
            fundamental_assessment=fundamental_assessment,
            technical_assessment=technical_assessment,
            pending_synthesis=pending_synthesis,
            typed_synthesis=typed_synthesis,
            as_of=effective_as_of or request.as_of_date,
            snapshot_at=snapshot_at,
            fundamental_request=fundamental_request,
            cross_domain_tension=cross_domain_tension,
            supplemental_evidence_chunks=supplemental_evidence_chunks,
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
            adk_token_usage=token_usage,
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
                run_id=run_id,
                request=request,
                response=response,
                baseline=baseline,
                critique_result=critique_result,
                effect=effect,
                pre_revision_objections=deterministic_objections,
                pending_synthesis=pending_synthesis,
                critic_step=critic_step,
                additional_token_usage=additional_token_usage,
                warnings=warnings,
                typed_synthesis=typed_synthesis,
                fundamental_assessment=fundamental_assessment,
                technical_assessment=technical_assessment,
                effective_as_of=effective_as_of,
                snapshot_at=snapshot_at,
                fundamental_request=fundamental_request,
                cross_domain_tension=cross_domain_tension,
                supplemental_evidence_chunks=supplemental_evidence_chunks,
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
        adk_token_usage: dict[str, int],
    ) -> _PendingTraceStep:
        objections = [
            *deterministic_objections,
            *critique_result.response.objections,
        ]
        output = {
            "source": "typed_critic_view",
            "adk_token_usage": dict(adk_token_usage),
            "status": critique_result.response.status,
            "verdict": critique_result.response.verdict,
            "objection_counts_by_severity": _objection_counts_by_severity(objections),
            "objections": _serialize_critic_objections(objections),
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
            detail="; ".join(
                [
                    *critique_result.validation.reason_codes,
                    *critique_result.validation_diagnostics,
                ]
            ),
            tool_calls=tool_calls,
            output=output,
            latency_ms=latency_ms,
            token_usage=token_usage,
        )

    def _apply_bounded_revision(
        self,
        *,
        run_id: str,
        request: AnalyzeRequest,
        response: AnalyzeResponse,
        baseline: DraftRecommendation,
        critique_result: CritiqueResult,
        effect: Any,
        pre_revision_objections: Sequence[CritiqueObjection],
        pending_synthesis: _PendingTraceStep | None,
        critic_step: _PendingTraceStep,
        additional_token_usage: dict[str, int],
        warnings: list[str],
        typed_synthesis: _TypedSynthesisRun | None = None,
        fundamental_assessment: FundamentalAssessmentResult | None = None,
        technical_assessment: TechnicalAssessmentResult | None = None,
        effective_as_of: date | None = None,
        snapshot_at: datetime | None = None,
        fundamental_request: FundamentalAnalysisRequest | None = None,
        cross_domain_tension: CrossDomainTension | None = None,
        supplemental_evidence_chunks: Sequence[EvidenceChunkInput] = (),
    ) -> int:
        runtime = self._build_blackboard_runtime(
            fundamental_mode=_fundamental_agent_mode(),
            technical_mode=_technical_agent_mode(),
            synthesizer_contract=_synthesizer_contract(),
            critic_mode=_critic_mode(),
        )
        outcome = run_bounded_critic_revision(
            runtime=runtime,
            ticker=request.ticker,
            run_id=run_id,
            request=request,
            response=response,
            baseline=baseline,
            critique_result=critique_result,
            effect=effect,
            pre_revision_objections=pre_revision_objections,
            fundamental_assessment=fundamental_assessment,
            technical_assessment=technical_assessment,
            fundamental_request=fundamental_request,
            cross_domain_tension=cross_domain_tension,
            supplemental_evidence_chunks=supplemental_evidence_chunks,
            effective_as_of=effective_as_of,
            snapshot_at=snapshot_at,
            typed_synthesis_request=typed_synthesis.request if typed_synthesis is not None else None,
            typed_results_len=len(typed_synthesis.results) if typed_synthesis is not None else 0,
            draft_memo_text=self._synthesizer_memo(pending_synthesis),
            session_state={},
            warnings=warnings,
            typed_synthesis_obj=typed_synthesis,
        )
        _merge_token_usage(additional_token_usage, outcome.token_usage)
        self._apply_revision_outcome_to_steps(
            outcome=outcome,
            critique_result=critique_result,
            pending_synthesis=pending_synthesis,
            critic_step=critic_step,
            typed_synthesis=typed_synthesis,
        )
        return outcome.revisions_performed

    @staticmethod
    def _apply_revision_outcome_to_steps(
        *,
        outcome: BoundedRevisionOutcome,
        critique_result: CritiqueResult,
        pending_synthesis: _PendingTraceStep | None,
        critic_step: _PendingTraceStep,
        typed_synthesis: _TypedSynthesisRun | None,
    ) -> None:
        routing_payload = {
            "revision_objection_ids": list(outcome.routing.revision_objection_ids),
            "revision_targets": [
                target.model_dump(mode="json") for target in outcome.routing.revision_targets
            ],
        }
        if critic_step.output is not None:
            critic_step.output.update(routing_payload)

        if outcome.rejected:
            if pending_synthesis is not None:
                pending_synthesis.status = "degraded"
                pending_synthesis.reason = outcome.error_reason or "critic_revision_failed"
                pending_synthesis.detail = outcome.error_detail
                pending_synthesis.output = {
                    **(pending_synthesis.output or {}),
                    "revision_instructions": critique_result.response.revision_instructions,
                    "revision_text": outcome.raw_output,
                    **routing_payload,
                }
            critic_step.status = "degraded"
            critic_step.reason = outcome.error_reason or "critic_revision_failed"
            critic_step.detail = outcome.error_detail
            return

        if typed_synthesis is not None and outcome.typed_request is not None and outcome.typed_result is not None:
            typed_synthesis.record_revision(outcome.typed_request, outcome.typed_result)
        if pending_synthesis is not None:
            pending_synthesis.output = {
                **(pending_synthesis.output or {}),
                "revision_guardrail": "applied",
                "revision_text": outcome.raw_output,
                **routing_payload,
            }
            pending_synthesis.latency_ms += outcome.latency_ms
            if typed_synthesis is None:
                pending_synthesis.token_usage = _merged_token_usage(
                    pending_synthesis.token_usage,
                    outcome.token_usage,
                )

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
        critic_findings: list[str] | None = None,
        critic_objections: Sequence[CritiqueObjection] = (),
        revision_objection_ids: Sequence[str] = (),
        revision_targets: Sequence[CritiqueRevisionTarget] = (),
        typed_synthesis: _TypedSynthesisRun | None = None,
        typed_request: DecisionSynthesisRequest | None = None,
    ) -> _DecisionRevisionResult:
        del ticker
        if typed_synthesis is not None or typed_request is not None:
            return self._request_typed_synthesis_revision(
                response=response,
                typed_synthesis=typed_synthesis,
                typed_request=typed_request,
                critic_findings=critic_findings or [revision_instructions],
                critic_objections=critic_objections,
                revision_objection_ids=revision_objection_ids,
                revision_targets=revision_targets,
            )
        prompt = self._build_revision_prompt(
            request=request,
            response=response,
            baseline=baseline,
            revision_instructions=revision_instructions,
            critic_objections=critic_objections,
            revision_objection_ids=revision_objection_ids,
            revision_targets=revision_targets,
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

    def _request_typed_synthesis_revision(
        self,
        *,
        response: AnalyzeResponse,
        typed_synthesis: _TypedSynthesisRun | None,
        critic_findings: list[str],
        typed_request: DecisionSynthesisRequest | None = None,
        critic_objections: Sequence[CritiqueObjection] = (),
        revision_objection_ids: Sequence[str] = (),
        revision_targets: Sequence[CritiqueRevisionTarget] = (),
    ) -> _DecisionRevisionResult:
        previous_request = typed_request or (
            typed_synthesis.request if typed_synthesis is not None else None
        )
        if previous_request is None:
            raise ValueError("typed_request or typed_synthesis is required for typed revision.")
        revision_request = build_synthesis_request(
            run_id=previous_request.run_id,
            response=response,
            analyst_views=previous_request.analyst_views,
            as_of_date=previous_request.as_of_date,
            snapshot_at=previous_request.snapshot_at,
            critic_findings=critic_findings,
            critic_objections=critic_objections,
            revision_objection_ids=revision_objection_ids,
            revision_targets=revision_targets,
            cross_domain_tension=previous_request.cross_domain_tension,
            supplemental_evidence_chunks=previous_request.supplemental_evidence_chunks,
        )
        started_at = time.monotonic()
        result = self._typed_synthesizer().synthesize(revision_request)
        return _DecisionRevisionResult(
            synthesis=DecisionSynthesis(
                decision=result.response.proposed_decision,
                confidence=result.response.confidence,
                reasons=[item.claim for item in result.response.key_reasons],
            ),
            raw_output=result.rendered_memo,
            token_usage=result.token_usage,
            latency_ms=(time.monotonic() - started_at) * 1000,
            typed_request=revision_request,
            typed_result=result,
        )

    def _build_revision_prompt(
        self,
        *,
        request: AnalyzeRequest,
        response: AnalyzeResponse,
        baseline: DraftRecommendation,
        revision_instructions: str,
        critic_objections: Sequence[CritiqueObjection] = (),
        revision_objection_ids: Sequence[str] = (),
        revision_targets: Sequence[CritiqueRevisionTarget] = (),
    ) -> str:
        payload: dict[str, Any] = {
            "ticker": request.ticker,
            "as_of_date": request.as_of_date.isoformat() if request.as_of_date is not None else None,
            "baseline": baseline.model_dump(mode="json"),
            "current_draft": build_draft_from_response(
                response,
                source="decision_synthesizer",
            ).model_dump(mode="json"),
            "revision_instructions": revision_instructions,
        }
        if critic_objections:
            payload["critic_objections"] = [obj.model_dump(mode="json") for obj in critic_objections]
        if revision_objection_ids:
            payload["revision_objection_ids"] = list(revision_objection_ids)
        if revision_targets:
            payload["revision_targets"] = [target.model_dump(mode="json") for target in revision_targets]
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
        *,
        deferred_technical: tuple[_TechnicalAssessmentOutcome, AnalyzeResponse, AnalyzeResponse] | None = None,
    ) -> None:
        observed = set(observed_agents)
        pending_deferred = deferred_technical
        for agent_name, reason in _SPECIALIST_SKIP_REASONS.items():
            if agent_name == TECHNICAL_ANALYST_NAME and pending_deferred is not None:
                self._flush_deferred_technical(trace_recorder, pending_deferred)
                pending_deferred = None
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

    def _assess_specialists(
        self,
        *,
        run_id: str,
        request: AnalyzeRequest,
        as_of: date,
    ) -> tuple[_FundamentalAssessmentOutcome | None, _TechnicalAssessmentOutcome | None]:
        fundamental_mode = _fundamental_agent_mode()
        technical_mode = _technical_agent_mode()
        if fundamental_mode != "off" and technical_mode != "off":
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="specialist") as executor:
                fundamental_future = executor.submit(
                    self._run_fundamental_assessment,
                    mode=fundamental_mode,
                    ticker=request.ticker,
                    run_id=run_id,
                    as_of=as_of,
                )
                technical_future = executor.submit(
                    self._run_technical_assessment,
                    mode=technical_mode,
                    ticker=request.ticker,
                    run_id=run_id,
                    as_of=as_of,
                )
                return fundamental_future.result(), technical_future.result()

        fundamental_outcome = (
            self._run_fundamental_assessment(
                mode=fundamental_mode,
                ticker=request.ticker,
                run_id=run_id,
                as_of=as_of,
            )
            if fundamental_mode != "off"
            else None
        )
        technical_outcome = (
            self._run_technical_assessment(
                mode=technical_mode,
                ticker=request.ticker,
                run_id=run_id,
                as_of=as_of,
            )
            if technical_mode != "off"
            else None
        )
        return fundamental_outcome, technical_outcome

    def _build_run_critique_request(
        self,
        *,
        ticker: str,
        run_id: str,
        response: AnalyzeResponse,
        fundamental_assessment: FundamentalAssessmentResult | None,
        technical_assessment: TechnicalAssessmentResult | None,
        pending_synthesis: _PendingTraceStep | None,
        typed_synthesis: _TypedSynthesisRun | None,
        as_of: date | None,
        snapshot_at: datetime | None,
        fundamental_request: FundamentalAnalysisRequest | None,
        cross_domain_tension: CrossDomainTension | None,
        supplemental_evidence_chunks: Sequence[EvidenceChunkInput],
    ) -> CritiqueRequest:
        critique_kwargs: dict[str, Any] = {
            "ticker": ticker,
            "run_id": run_id,
            "draft": build_draft_from_response(
                response,
                memo=self._synthesizer_memo(pending_synthesis),
                memo_claims=typed_synthesis.memo_claims() if typed_synthesis is not None else (),
                source="decision_synthesizer",
            ),
            "analyst_views": build_analyst_views(
                fundamental=fundamental_assessment, technical=technical_assessment
            ),
            "deterministic_warnings": response.warnings,
            "deterministic_diagnosis": response.diagnosis,
            "as_of": as_of,
            "engine": self.engine,
            "fundamental_request": fundamental_request,
            "cross_domain_tension": cross_domain_tension,
            "supplemental_evidence_chunks": supplemental_evidence_chunks,
            "snapshot_at": snapshot_at,
        }
        if self.embedding_client is not None and callable(getattr(self.embedding_client, "embed_texts", None)):
            critique_kwargs["embedding_client"] = self.embedding_client
        return build_critique_request(**critique_kwargs)

    def _resolve_cross_domain_tension(
        self,
        *,
        ticker: str,
        snapshot_at: datetime,
        fundamental_outcome: _FundamentalAssessmentOutcome | None,
        technical_outcome: _TechnicalAssessmentOutcome | None,
        fundamental_assessment: FundamentalAssessmentResult | None,
        technical_assessment: TechnicalAssessmentResult | None,
        trace_recorder: TraceRecorder,
    ) -> CrossDomainTensionResolution:
        started_at = time.monotonic()
        resolution = resolve_cross_domain_tension(
            ticker=ticker,
            snapshot_at=snapshot_at,
            fundamental_assessment=fundamental_assessment,
            technical_assessment=technical_assessment,
            fundamental_request=fundamental_outcome.context if fundamental_outcome is not None else None,
            technical_request=technical_outcome.context if technical_outcome is not None else None,
            embedding_client=self.embedding_client,
            engine=self.engine,
        )
        if resolution.tension is not None:
            trace_recorder.record_completed(
                "cross_domain_tension",
                latency_ms=(time.monotonic() - started_at) * 1000,
                token_usage={},
                output={
                    "conflict_type": resolution.tension.conflict_type,
                    "fundamental_signal": resolution.tension.fundamental_signal,
                    "technical_signal": resolution.tension.technical_signal,
                    "supplemental_chunk_ids": list(resolution.tension.supplemental_chunk_ids),
                    "date_window_days": resolution.tension.date_window_days,
                },
            )
        return resolution

    def _run_fundamental_assessment(
        self,
        *,
        mode: Literal["shadow", "enforced"],
        ticker: str,
        run_id: str,
        as_of: date | None,
    ) -> _FundamentalAssessmentOutcome:
        context_started_at = time.monotonic()
        context_kwargs: dict[str, Any] = {
            "as_of": as_of,
            "engine": self.engine,
        }
        if self.embedding_client is not None and callable(getattr(self.embedding_client, "embed_texts", None)):
            context_kwargs["embedding_client"] = self.embedding_client
        context = build_fundamental_analysis_request(
            ticker,
            run_id,
            **context_kwargs,
        )
        context_latency_ms = (time.monotonic() - context_started_at) * 1000

        assessment_started_at = time.monotonic()
        assessment_result = FundamentalAnalyst().assess(context)
        assessment_latency_ms = (time.monotonic() - assessment_started_at) * 1000
        return _FundamentalAssessmentOutcome(
            mode=mode,
            context=context,
            context_latency_ms=context_latency_ms,
            assessment_result=assessment_result,
            assessment_latency_ms=assessment_latency_ms,
        )

    def _run_technical_assessment(
        self,
        *,
        mode: Literal["shadow", "enforced"],
        ticker: str,
        run_id: str,
        as_of: date | None,
    ) -> _TechnicalAssessmentOutcome:
        context_started_at = time.monotonic()
        context = build_technical_analysis_request(
            ticker,
            run_id,
            as_of=as_of,
            engine=self.engine,
        )
        context_latency_ms = (time.monotonic() - context_started_at) * 1000

        assessment_started_at = time.monotonic()
        assessment_result = TechnicalAnalyst().assess(context)
        assessment_latency_ms = (time.monotonic() - assessment_started_at) * 1000
        return _TechnicalAssessmentOutcome(
            mode=mode,
            context=context,
            context_latency_ms=context_latency_ms,
            assessment_result=assessment_result,
            assessment_latency_ms=assessment_latency_ms,
        )

    @staticmethod
    def _record_fundamental_steps_from_state(
        *,
        fund_req: FundamentalAnalysisRequest,
        fund_res: FundamentalAssessmentResult,
        context_latency_ms: float,
        assessment_latency_ms: float,
        trace_recorder: TraceRecorder,
    ) -> None:
        trace_recorder.record_completed(
            "fundamental_context_builder",
            latency_ms=context_latency_ms,
            output={
                "context_hash": fund_req.context_hash,
                "chunk_count": len(fund_req.evidence_chunks),
            },
        )
        if fund_res.validation.accepted:
            trace_recorder.record_completed(
                FUNDAMENTAL_ANALYST_NAME,
                latency_ms=assessment_latency_ms,
                token_usage=fund_res.token_usage,
                output={
                    "status": fund_res.response.status,
                    "proposed_score_adjustment": fund_res.response.proposed_score_adjustment,
                },
            )
        else:
            trace_recorder.record_degraded(
                FUNDAMENTAL_ANALYST_NAME,
                "assessment_invalid",
                detail=",".join(fund_res.validation.reason_codes),
                latency_ms=assessment_latency_ms,
                token_usage=fund_res.token_usage,
                output={"status": fund_res.response.status},
            )

    def _fold_fundamental_outcome(
        self,
        *,
        outcome: _FundamentalAssessmentOutcome | None,
        response: AnalyzeResponse,
        trace_recorder: TraceRecorder,
    ) -> FundamentalAssessmentResult | None:
        if outcome is None:
            return None

        assessment_result = outcome.assessment_result
        self._record_fundamental_steps_from_state(
            fund_req=outcome.context,
            fund_res=assessment_result,
            context_latency_ms=outcome.context_latency_ms,
            assessment_latency_ms=outcome.assessment_latency_ms,
            trace_recorder=trace_recorder,
        )

        effect = apply_fundamental_policy(
            mode=outcome.mode,
            deterministic_response=response.model_copy(deep=True),
            request=outcome.context,
            assessment_result=assessment_result,
        )
        updated_effect = self._apply_policy_effect_to_response(effect, response)
        response.fundamental_agent_effect = updated_effect
        return assessment_result

    def _apply_policy_effect_to_response(
        self,
        effect,
        response: AnalyzeResponse,
    ):
        self._apply_downgrade_only_transition(
            response=response,
            proposed_decision=effect.final_decision,
            proposed_confidence=response.confidence,
        )
        effect.final_decision = response.decision
        effect.decision_changed = effect.final_decision != effect.baseline_decision
        return effect

    @staticmethod
    def _record_technical_steps_from_state(
        *,
        tech_req: TechnicalAnalysisRequest,
        tech_res: TechnicalAssessmentResult,
        context_latency_ms: float,
        assessment_latency_ms: float,
        trace_recorder: TraceRecorder,
    ) -> None:
        trace_recorder.record_completed(
            "technical_context_builder",
            latency_ms=context_latency_ms,
            output={
                "context_hash": tech_req.context_hash,
                "fact_count": len(tech_req.facts),
                "bar_count": tech_req.coverage.bar_count,
            },
        )
        if tech_res.validation.accepted:
            trace_recorder.record_completed(
                TECHNICAL_ANALYST_NAME,
                latency_ms=assessment_latency_ms,
                token_usage=tech_res.token_usage,
                output={
                    "status": tech_res.response.status,
                    "proposed_score_adjustment": tech_res.response.proposed_score_adjustment,
                    "trend_state": tech_res.response.trend_state,
                },
            )
        else:
            trace_recorder.record_degraded(
                TECHNICAL_ANALYST_NAME,
                "assessment_invalid",
                detail=",".join(tech_res.validation.reason_codes),
                latency_ms=assessment_latency_ms,
                token_usage=tech_res.token_usage,
                output={"status": tech_res.response.status},
            )

    def _flush_deferred_technical(
        self,
        trace_recorder: TraceRecorder,
        deferred_technical: tuple[_TechnicalAssessmentOutcome, AnalyzeResponse, AnalyzeResponse] | None,
    ) -> None:
        if deferred_technical is None:
            return None
        outcome, _det_resp, _resp = deferred_technical
        self._record_technical_steps_from_state(
            tech_req=outcome.context,
            tech_res=outcome.assessment_result,
            context_latency_ms=outcome.context_latency_ms,
            assessment_latency_ms=outcome.assessment_latency_ms,
            trace_recorder=trace_recorder,
        )
        return None

    def _fold_technical_outcome(
        self,
        *,
        outcome: _TechnicalAssessmentOutcome | None,
        deterministic_response: AnalyzeResponse,
        response: AnalyzeResponse,
        trace_recorder: TraceRecorder,
        record_trace: bool = True,
    ) -> TechnicalAssessmentResult | None:
        if outcome is None:
            return None

        assessment_result = outcome.assessment_result
        if record_trace:
            self._record_technical_steps_from_state(
                tech_req=outcome.context,
                tech_res=assessment_result,
                context_latency_ms=outcome.context_latency_ms,
                assessment_latency_ms=outcome.assessment_latency_ms,
                trace_recorder=trace_recorder,
            )

        effect = apply_technical_policy(
            mode=outcome.mode,
            deterministic_response=deterministic_response,
            post_fundamental_response=response.model_copy(deep=True),
            request=outcome.context,
            assessment_result=assessment_result,
        )
        _apply_technical_effect_to_response(effect, response)
        response.technical_agent_effect = effect
        self.last_technical_assessment = assessment_result
        self.last_technical_request = outcome.context
        return assessment_result

    def _build_trace(
        self,
        *,
        run_id: str,
        ticker: str,
        as_of_date: date | None,
        trace_recorder: TraceRecorder,
        response: AnalyzeResponse,
        token_usage: dict[str, int],
        warnings: list[str],
        total_latency_ms: float,
        adk_event_count: int,
        observed_agents: list[str],
        typed_synthesis: _TypedSynthesisRun | None = None,
    ) -> AnalysisTrace:
        steps = trace_recorder.steps()
        embedding_usage = embedding_usage_of(self.embedding_client)
        cost, cost_breakdown = price_trace(
            steps,
            token_usage,
            embedding_model=embedding_usage.model if embedding_usage is not None else None,
            embedding_characters=self._embedding_characters() - self._embedding_characters_at_start,
        )
        return AnalysisTrace(
            run_id=run_id,
            ticker=ticker,
            as_of_date=as_of_date,
            as_of_date_source="request" if as_of_date is not None else None,
            steps=steps,
            final_decision=response.decision,
            total_latency_ms=total_latency_ms,
            token_usage=token_usage,
            estimated_cost_usd=cost.usd,
            cost_status=cost.status,
            cost_breakdown=cost_breakdown,
            pricing_version=cost.pricing_version,
            warnings=warnings,
            entered_agent_layer=adk_event_count > 0,
            adk_event_count=adk_event_count,
            observed_agents=observed_agents,
            fundamental_agent_effect=response.fundamental_agent_effect,
            technical_agent_effect=response.technical_agent_effect,
            critic_effect=response.critic_effect,
            synthesizer_effect=(
                typed_synthesis.effect(response.decision) if typed_synthesis is not None else None
            ),
        )

    def _embedding_characters(self) -> int:
        usage = embedding_usage_of(self.embedding_client)
        return usage.characters if usage is not None else 0

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
        from app.observability.tracing import current_trace_id

        trace.trace_id = current_trace_id() or trace.trace_id
        if trace.trace_id is not None:
            from opentelemetry import trace as otel_trace

            active_span = otel_trace.get_current_span()
            if active_span.is_recording():
                active_span.set_attribute("forseti.run_id", trace.run_id)
                active_span.set_attribute("ticker", trace.ticker)
        run = AgentRun(
            run_id=trace.run_id,
            trace_id=trace.trace_id,
            ticker=trace.ticker,
            created_at=datetime.now(timezone.utc),
            as_of_date=trace.as_of_date,
            as_of_date_source=trace.as_of_date_source or (
                "request" if trace.as_of_date is not None else None
            ),
            final_decision=trace.final_decision,
            total_latency_ms=trace.total_latency_ms,
            token_usage=trace.token_usage,
            warnings=trace.warnings,
            entered_agent_layer=trace.entered_agent_layer,
            adk_event_count=trace.adk_event_count,
            observed_agents=trace.observed_agents,
            cost_usd=(
                Decimal(str(trace.estimated_cost_usd))
                if trace.estimated_cost_usd is not None
                else None
            ),
            cost_breakdown=trace.cost_breakdown,
            pricing_version=trace.pricing_version,
            fundamental_agent_effect=(
                trace.fundamental_agent_effect.model_dump(mode="json")
                if trace.fundamental_agent_effect is not None
                else None
            ),
            technical_agent_effect=(
                trace.technical_agent_effect.model_dump(mode="json")
                if trace.technical_agent_effect is not None
                else None
            ),
            critic_effect=(
                trace.critic_effect.model_dump(mode="json")
                if trace.critic_effect is not None
                else None
            ),
            synthesizer_effect=(
                trace.synthesizer_effect.model_dump(mode="json")
                if trace.synthesizer_effect is not None
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
        trace_id=run.trace_id,
        ticker=run.ticker,
        as_of_date=resolve_agent_run_as_of_date(run),
        as_of_date_source=run.as_of_date_source,
        steps=steps,
        final_decision=final_decision,
        total_latency_ms=run.total_latency_ms,
        token_usage=run.token_usage,
        warnings=run.warnings,
        entered_agent_layer=run.entered_agent_layer,
        adk_event_count=run.adk_event_count,
        observed_agents=run.observed_agents,
        estimated_cost_usd=float(run.cost_usd) if run.cost_usd is not None else None,
        cost_status=(run.cost_breakdown or {}).get("status"),
        cost_breakdown=run.cost_breakdown,
        pricing_version=run.pricing_version,
        fundamental_agent_effect=run.fundamental_agent_effect,
        technical_agent_effect=run.technical_agent_effect,
        critic_effect=run.critic_effect,
        synthesizer_effect=run.synthesizer_effect,
    )
