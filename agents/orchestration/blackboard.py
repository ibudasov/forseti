"""Unified ADK BaseAgent blackboard spine and post-revision deterministic gate."""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timezone
import json
import threading
import time
from typing import Any, AsyncGenerator, Callable, Iterable, Literal, Mapping, Optional, Sequence, TypeVar, cast
from uuid import uuid4

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, SkipValidation, ValidationError

from agents.config import HARD_RULES_TEXT, AgentWorkflowConfig
from agents.orchestration.adk_events import event_text
from agents.orchestration.constants import (
    ANALYSIS_RESPONSE_KEY,
    BASELINE_DRAFT_KEY,
    CRITIC_EFFECT_KEY,
    CRITIC_REVISION_COUNT_KEY,
    CRITIC_STEP_META_KEY,
    CRITIQUE_OUTPUT_KEY,
    CRITIQUE_REQUEST_KEY,
    CRITIQUE_RESULT_KEY,
    CROSS_DOMAIN_TENSION_KEY,
    DECISION_MEMO_RESULT_KEY,
    DECISION_SYNTHESIS_REQUEST_KEY,
    DETERMINISTIC_OBJECTIONS_KEY,
    DETERMINISTIC_RESPONSE_KEY,
    DRAFT_RECOMMENDATION_OUTPUT_KEY,
    EFFECTIVE_AS_OF_KEY,
    FUNDAMENTAL_ASSESSMENT_KEY,
    FUNDAMENTAL_ASSESSMENT_LATENCY_MS_KEY,
    FUNDAMENTAL_CONTEXT_LATENCY_MS_KEY,
    FUNDAMENTAL_EFFECT_KEY,
    FUNDAMENTAL_REQUEST_KEY,
    FUNDAMENTAL_VIEW_OUTPUT_KEY,
    POST_REVISION_OBJECTIONS_KEY,
    RUN_ID_KEY,
    SNAPSHOT_AT_KEY,
    SUPPLEMENTAL_EVIDENCE_CHUNKS_KEY,
    SYNTHESIZER_EFFECT_KEY,
    TECHNICAL_ASSESSMENT_KEY,
    TECHNICAL_ASSESSMENT_LATENCY_MS_KEY,
    TECHNICAL_CONTEXT_LATENCY_MS_KEY,
    TECHNICAL_EFFECT_KEY,
    TECHNICAL_REQUEST_KEY,
    TECHNICAL_VIEW_OUTPUT_KEY,
    TENSION_LATENCY_MS_KEY,
    TICKER_KEY,
    WORKFLOW_WARNINGS_KEY,
)
from agents.orchestration.typed_view_agent import render_fundamental_view, render_technical_view
from agents.tools.ticker_resolver import resolve_ticker
from app.rag.embedding import EmbeddingClient, build_configured_embedding_client
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from app.schemas.critique import (
    CriticEffect,
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
from app.schemas.technicals import (
    TechnicalAgentEffect,
    TechnicalAnalysisRequest,
    TechnicalAssessmentResult,
)
from app.schemas.tension import CrossDomainTension
from app.services.analyzer import analyze_request
from app.services.critic import Critic, RetryableModelError
from app.services.critic_policy import apply_critic_policy, evaluate_post_revision_policy
from app.services.critic_rules import evaluate_deterministic_objections
from app.services.critique_context import (
    build_analyst_views,
    build_critique_request,
    build_draft_from_response,
)
from app.services.cross_domain_tension import CrossDomainTensionResolution, resolve_cross_domain_tension
from app.services.decision_synthesizer import DecisionSynthesizer, build_decision_memo, memo_hash
from app.services.fundamental_analyst import FundamentalAnalyst
from app.services.fundamental_context import build_fundamental_analysis_request
from app.services.fundamental_policy import apply_fundamental_policy
from app.services.synthesis_context import build_synthesis_request
from app.services.technical_analyst import TechnicalAnalyst
from app.services.technical_context import build_technical_analysis_request
from app.services.technical_policy import apply_technical_policy
from app.settings import get_settings

MAX_CRITIC_REVISIONS = 1
_RISK_FIELDS = (
    "entry_range",
    "stop_loss",
    "take_profit",
    "risk_reward",
    "position_size_eur",
)
_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}
_ModelT = TypeVar("_ModelT", bound=BaseModel)


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
class _DecisionRevisionResult:
    synthesis: DecisionSynthesis | None
    raw_output: str
    token_usage: dict[str, int]
    latency_ms: float
    error_reason: str | None = None
    error_detail: str | None = None
    typed_request: DecisionSynthesisRequest | None = None
    typed_result: DecisionMemoResult | None = None


@dataclass(frozen=True)
class _FundamentalAssessmentOutcome:
    mode: Literal["shadow", "enforced"]
    context: FundamentalAnalysisRequest
    context_latency_ms: float
    assessment_result: FundamentalAssessmentResult
    assessment_latency_ms: float


@dataclass(frozen=True)
class _TechnicalAssessmentOutcome:
    mode: Literal["shadow", "enforced"]
    context: TechnicalAnalysisRequest
    context_latency_ms: float
    assessment_result: TechnicalAssessmentResult
    assessment_latency_ms: float


@dataclass(frozen=True)
class RevisionRouting:
    critic_objections: list[CritiqueObjection]
    revision_objection_ids: list[str]
    revision_targets: list[CritiqueRevisionTarget]
    critic_findings: list[str]


@dataclass(frozen=True)
class _RevisionSnapshot:
    decision: Literal["trade", "watchlist", "no_trade"]
    confidence: float
    reasons: list[str]
    warnings: list[str]
    risk_fields: dict[str, Any]
    memo: DecisionMemo | None
    typed_results_len: int
    state_draft: Any
    has_state_draft: bool
    pre_objections: list[CritiqueObjection]


@dataclass(frozen=True)
class BoundedRevisionOutcome:
    revisions_performed: int
    rejected: bool
    raw_output: str
    latency_ms: float
    token_usage: dict[str, int]
    routing: RevisionRouting
    post_objections: list[CritiqueObjection]
    error_reason: str | None = None
    error_detail: str | None = None
    typed_request: DecisionSynthesisRequest | None = None
    typed_result: DecisionMemoResult | None = None
    gate_effect: CriticEffect | None = None


def write_state_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def write_state_models(models: Sequence[BaseModel]) -> list[dict[str, Any]]:
    return [model.model_dump(mode="json") for model in models]


def read_state_model(state: Mapping[str, Any], key: str, model_type: type[_ModelT]) -> _ModelT | None:
    raw = state.get(key)
    if raw is None:
        return None
    if isinstance(raw, model_type):
        return raw
    return model_type.model_validate(raw)


def read_state_models(state: Mapping[str, Any], key: str, model_type: type[_ModelT]) -> list[_ModelT]:
    raw = state.get(key)
    if not raw:
        return []
    return [
        item if isinstance(item, model_type) else model_type.model_validate(item)
        for item in raw
    ]


def validate_risk_output(proposal: DecisionSynthesis, deterministic: AnalyzeResponse) -> None:
    """Reject a specialist proposal that fabricates or changes trade numbers."""
    for field_name in _RISK_FIELDS:
        proposed_value = getattr(proposal, field_name)
        deterministic_value = getattr(deterministic, field_name)
        if proposed_value is not None and proposed_value != deterministic_value:
            raise ValueError(f"agent_output_changed_risk_value: {field_name}")


def _decision_rank(decision: str) -> int:
    return _DECISION_RANK[decision]


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
    for field_name in _RISK_FIELDS:
        if getattr(proposal, field_name) is None:
            setattr(proposal, field_name, getattr(deterministic, field_name))
    return proposal


def apply_downgrade_only_transition(
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
    was_changed = next_decision != response.decision or next_confidence != response.confidence
    response.decision = next_decision
    response.confidence = next_confidence
    return was_changed


def apply_technical_effect_to_response(effect: TechnicalAgentEffect, response: AnalyzeResponse) -> None:
    """Mutate ``response`` for an enforced technical downgrade."""
    if effect.final_decision == response.decision:
        return
    response.decision = effect.final_decision
    response.confidence = effect.final_confidence
    if effect.final_decision == "trade":
        return
    response.entry_range = None
    response.stop_loss = None
    response.take_profit = None
    response.risk_reward = None
    response.position_size_eur = None
    response.time_stop_at = None


def parse_legacy_synthesis(text: str) -> DecisionSynthesis | None:
    if not text.strip():
        return None
    try:
        return DecisionSynthesis.model_validate_json(text)
    except ValidationError:
        return None


def _critic_findings(critique_result: CritiqueResult) -> list[str]:
    response = critique_result.response
    findings = [response.revision_instructions, *(objection.claim for objection in response.objections)]
    return [finding for finding in findings if finding.strip()]


def build_critic_revision_routing(
    critique_result: CritiqueResult,
    deterministic_objections: Sequence[CritiqueObjection],
) -> RevisionRouting:
    combined: list[CritiqueObjection] = []
    seen_ids: set[str] = set()
    for objection in (*critique_result.response.objections, *deterministic_objections):
        if objection.objection_id in seen_ids:
            continue
        seen_ids.add(objection.objection_id)
        combined.append(objection)

    explicit_ids = list(critique_result.response.revision_objection_ids)
    if explicit_ids:
        target_ids = list(
            dict.fromkeys([*explicit_ids, *(obj.objection_id for obj in deterministic_objections)])
        )
    else:
        target_ids = [objection.objection_id for objection in combined]

    return RevisionRouting(
        critic_objections=combined,
        revision_objection_ids=target_ids,
        revision_targets=list(critique_result.response.revision_targets),
        critic_findings=_critic_findings(critique_result),
    )


def _objection_counts_by_severity(objections: Iterable[CritiqueObjection]) -> dict[str, int]:
    return dict(Counter(objection.severity for objection in objections))


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


def _merged_token_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    merged = dict(left)
    for field_name, value in right.items():
        merged[field_name] = merged.get(field_name, 0) + value
    return merged


def _can_embed(client: Any) -> bool:
    return client is not None and callable(getattr(client, "embed_texts", None))


async def _run_blocking_cancellable(fn: Callable[[], Any]) -> Any:
    loop = asyncio.get_running_loop()
    done = asyncio.Event()

    def _runner() -> Any:
        try:
            return fn()
        finally:
            loop.call_soon_threadsafe(done.set)

    task = asyncio.create_task(asyncio.to_thread(_runner))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not done.is_set():
            try:
                await done.wait()
            except asyncio.CancelledError:
                pass
        raise


def _extract_ticker_from_ctx(ctx: InvocationContext) -> str:
    state_ticker = ctx.session.state.get(TICKER_KEY)
    if isinstance(state_ticker, str) and state_ticker.strip():
        return state_ticker.strip()
    user_text = event_text({"content": getattr(ctx, "user_content", None)}).strip()
    candidate = user_text.split()[-1] if user_text else ""
    resolved = resolve_ticker(candidate)
    if resolved.is_valid and resolved.ticker:
        return resolved.ticker
    raise ValueError(resolved.error or "Unable to resolve ticker from session state or user message.")


@dataclass
class BlackboardRuntime:
    """Runtime ports and mode configuration shared across ADK blackboard nodes."""

    engine: Any = None
    embedding_client: Optional[EmbeddingClient] = None
    embedding_client_provider: Optional[Callable[[], Optional[EmbeddingClient]]] = None
    fundamental_mode: Literal["off", "shadow", "enforced"] = "off"
    technical_mode: Literal["off", "shadow", "enforced"] = "off"
    synthesizer_contract: Literal["legacy", "typed"] = "legacy"
    critic_mode: Literal["off", "shadow", "enforced"] = "off"
    config: Optional[AgentWorkflowConfig] = None
    decision_synthesizer_provider: Optional[Callable[[], DecisionSynthesizer]] = None
    analyze_request_fn: Callable[..., AnalyzeResponse] = analyze_request
    build_fundamental_request_fn: Callable[..., FundamentalAnalysisRequest] = build_fundamental_analysis_request
    fundamental_analyst_factory: Callable[[], Any] = FundamentalAnalyst
    build_technical_request_fn: Callable[..., TechnicalAnalysisRequest] = build_technical_analysis_request
    technical_analyst_factory: Callable[[], Any] = TechnicalAnalyst
    resolve_tension_fn: Callable[..., CrossDomainTensionResolution] = resolve_cross_domain_tension
    build_critique_request_fn: Callable[..., CritiqueRequest] = build_critique_request
    evaluate_deterministic_objections_fn: Callable[..., list[CritiqueObjection]] = evaluate_deterministic_objections
    review_critique_fn: Optional[Callable[[CritiqueRequest], CritiqueResult]] = None
    request_synthesis_revision_fn: Optional[Callable[..., _DecisionRevisionResult]] = None
    validate_risk_output_fn: Callable[[DecisionSynthesis, AnalyzeResponse], None] = validate_risk_output
    fundamental_mode_fn: Optional[Callable[[], Literal["off", "shadow", "enforced"]]] = None
    technical_mode_fn: Optional[Callable[[], Literal["off", "shadow", "enforced"]]] = None
    synthesizer_contract_fn: Optional[Callable[[], Literal["legacy", "typed"]]] = None
    critic_mode_fn: Optional[Callable[[], Literal["off", "shadow", "enforced"]]] = None
    _init_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _init_cache: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _lazy_embedder_resolved: bool = field(default=False, init=False, repr=False)
    _default_synthesizer: Optional[DecisionSynthesizer] = field(default=None, init=False, repr=False)

    def current_fundamental_mode(self) -> Literal["off", "shadow", "enforced"]:
        return self.fundamental_mode_fn() if self.fundamental_mode_fn is not None else self.fundamental_mode

    def current_technical_mode(self) -> Literal["off", "shadow", "enforced"]:
        return self.technical_mode_fn() if self.technical_mode_fn is not None else self.technical_mode

    def current_synthesizer_contract(self) -> Literal["legacy", "typed"]:
        return self.synthesizer_contract_fn() if self.synthesizer_contract_fn is not None else self.synthesizer_contract

    def current_critic_mode(self) -> Literal["off", "shadow", "enforced"]:
        return self.critic_mode_fn() if self.critic_mode_fn is not None else self.critic_mode

    def both_specialists_enabled(self) -> bool:
        return self.current_fundamental_mode() != "off" and self.current_technical_mode() != "off"

    def resolve_embedding_client(self) -> Optional[EmbeddingClient]:
        if self.embedding_client_provider is not None:
            return self.embedding_client_provider()
        if self.embedding_client is not None or self._lazy_embedder_resolved:
            return self.embedding_client
        with self._init_lock:
            if not self._lazy_embedder_resolved and self.embedding_client is None:
                self.embedding_client = build_configured_embedding_client()
                self._lazy_embedder_resolved = True
        return self.embedding_client

    def get_synthesizer(self) -> DecisionSynthesizer:
        if self.decision_synthesizer_provider is not None:
            return self.decision_synthesizer_provider()
        if self._default_synthesizer is None:
            self._default_synthesizer = DecisionSynthesizer()
        return self._default_synthesizer

    def review_critique(self, critique_request: CritiqueRequest) -> CritiqueResult:
        if self.review_critique_fn is not None:
            return self.review_critique_fn(critique_request)
        return Critic().review(critique_request)

    def request_synthesis_revision(self, **kwargs: Any) -> _DecisionRevisionResult:
        if self.request_synthesis_revision_fn is not None:
            return self.request_synthesis_revision_fn(**kwargs)
        return _default_request_synthesis_revision(self, **kwargs)

    def ensure_initialized(self, ctx: InvocationContext) -> dict[str, Any]:
        state = ctx.session.state
        if (
            DETERMINISTIC_RESPONSE_KEY in state
            and ANALYSIS_RESPONSE_KEY in state
            and RUN_ID_KEY in state
            and TICKER_KEY in state
        ):
            return {}
        invocation_key = getattr(ctx, "invocation_id", None) or str(id(ctx.session))
        with self._init_lock:
            cached = self._init_cache.get(invocation_key)
            if cached is None:
                ticker = _extract_ticker_from_ctx(ctx)
                run_id = str(state.get(RUN_ID_KEY) or uuid4())
                raw_as_of = state.get(EFFECTIVE_AS_OF_KEY)
                effective_as_of = (
                    date.fromisoformat(raw_as_of)
                    if isinstance(raw_as_of, str) and raw_as_of
                    else datetime.now(timezone.utc).date()
                )
                det_response = read_state_model(state, DETERMINISTIC_RESPONSE_KEY, AnalyzeResponse)
                if det_response is None:
                    det_response = self.analyze_request_fn(
                        AnalyzeRequest(ticker=ticker, as_of_date=None),
                        engine=self.engine,
                    )
                cached = {
                    TICKER_KEY: ticker,
                    RUN_ID_KEY: run_id,
                    EFFECTIVE_AS_OF_KEY: effective_as_of.isoformat(),
                    DETERMINISTIC_RESPONSE_KEY: write_state_model(det_response),
                    ANALYSIS_RESPONSE_KEY: write_state_model(det_response),
                    CRITIC_REVISION_COUNT_KEY: int(state.get(CRITIC_REVISION_COUNT_KEY, 0)),
                    WORKFLOW_WARNINGS_KEY: list(state.get(WORKFLOW_WARNINGS_KEY, [])),
                }
                self._init_cache[invocation_key] = cached
            return {key: value for key, value in cached.items() if key not in state}


def _default_request_synthesis_revision(
    runtime: BlackboardRuntime,
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
    typed_request: DecisionSynthesisRequest | None = None,
    **_: Any,
) -> _DecisionRevisionResult:
    del ticker
    if typed_request is not None:
        revision_request = build_synthesis_request(
            run_id=typed_request.run_id,
            response=response,
            analyst_views=typed_request.analyst_views,
            as_of_date=typed_request.as_of_date,
            snapshot_at=typed_request.snapshot_at,
            critic_findings=critic_findings or [revision_instructions],
            critic_objections=critic_objections,
            revision_objection_ids=revision_objection_ids,
            revision_targets=revision_targets,
            cross_domain_tension=typed_request.cross_domain_tension,
            supplemental_evidence_chunks=typed_request.supplemental_evidence_chunks,
        )
        started_at = time.monotonic()
        result = runtime.get_synthesizer().synthesize(revision_request)
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

    prompt = _build_legacy_revision_prompt(
        request=request,
        response=response,
        baseline=baseline,
        revision_instructions=revision_instructions,
        critic_objections=critic_objections,
        revision_objection_ids=revision_objection_ids,
        revision_targets=revision_targets,
    )
    settings = get_settings()
    started_at = time.monotonic()
    try:
        raw_output, token_usage = _call_gemini_legacy_revision(prompt, settings)
        synthesis = parse_legacy_synthesis(raw_output)
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
            raw_output=raw_output,
            token_usage=token_usage,
            latency_ms=(time.monotonic() - started_at) * 1000,
            error_reason="critic_revision_failed",
            error_detail="unparsable_synthesis",
        )
    return _DecisionRevisionResult(
        synthesis=synthesis,
        raw_output=raw_output,
        token_usage=token_usage,
        latency_ms=(time.monotonic() - started_at) * 1000,
    )


def _call_gemini_legacy_revision(prompt: str, settings: Any) -> tuple[str, dict[str, int]]:
    if not settings.VERTEX_AI_PROJECT:
        raise RetryableModelError("vertex_project_not_configured")
    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel

        vertexai.init(project=settings.VERTEX_AI_PROJECT, location=settings.VERTEX_AI_LOCATION)
        response = GenerativeModel(settings.GEMINI_MODEL).generate_content(
            prompt,
            generation_config={
                "temperature": settings.AGENT_MODEL_TEMPERATURE,
                "response_mime_type": "application/json",
            },
        )
    except Exception as exc:
        raise RetryableModelError(str(exc)) from exc
    usage_metadata = getattr(response, "usage_metadata", None)
    usage = (
        {
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
        if usage_metadata is not None
        else {}
    )
    return response.text, usage


def _build_legacy_revision_prompt(
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


def _run_fundamental_outcome(
    runtime: BlackboardRuntime,
    *,
    mode: Literal["shadow", "enforced"],
    ticker: str,
    run_id: str,
    as_of: date | None,
) -> _FundamentalAssessmentOutcome:
    context_started_at = time.monotonic()
    context_kwargs: dict[str, Any] = {"as_of": as_of, "engine": runtime.engine}
    embedder = runtime.resolve_embedding_client()
    if _can_embed(embedder):
        context_kwargs["embedding_client"] = embedder
    context = runtime.build_fundamental_request_fn(ticker, run_id, **context_kwargs)
    context_latency_ms = (time.monotonic() - context_started_at) * 1000

    assessment_started_at = time.monotonic()
    assessment_result = runtime.fundamental_analyst_factory().assess(context)
    assessment_latency_ms = (time.monotonic() - assessment_started_at) * 1000
    return _FundamentalAssessmentOutcome(
        mode=mode,
        context=context,
        context_latency_ms=context_latency_ms,
        assessment_result=assessment_result,
        assessment_latency_ms=assessment_latency_ms,
    )


def _run_technical_outcome(
    runtime: BlackboardRuntime,
    *,
    mode: Literal["shadow", "enforced"],
    ticker: str,
    run_id: str,
    as_of: date | None,
) -> _TechnicalAssessmentOutcome:
    context_started_at = time.monotonic()
    context = runtime.build_technical_request_fn(ticker, run_id, as_of=as_of, engine=runtime.engine)
    context_latency_ms = (time.monotonic() - context_started_at) * 1000

    assessment_started_at = time.monotonic()
    assessment_result = runtime.technical_analyst_factory().assess(context)
    assessment_latency_ms = (time.monotonic() - assessment_started_at) * 1000
    return _TechnicalAssessmentOutcome(
        mode=mode,
        context=context,
        context_latency_ms=context_latency_ms,
        assessment_result=assessment_result,
        assessment_latency_ms=assessment_latency_ms,
    )


def _resolve_snapshot_at_from_state(state: Mapping[str, Any], effective_as_of: date) -> datetime:
    raw_snapshot = state.get(SNAPSHOT_AT_KEY)
    if isinstance(raw_snapshot, str) and raw_snapshot:
        return datetime.fromisoformat(raw_snapshot)
    fundamental_req = read_state_model(state, FUNDAMENTAL_REQUEST_KEY, FundamentalAnalysisRequest)
    if fundamental_req is not None:
        return fundamental_req.snapshot_at
    technical_req = read_state_model(state, TECHNICAL_REQUEST_KEY, TechnicalAnalysisRequest)
    if technical_req is not None:
        return technical_req.snapshot_at
    return datetime.combine(effective_as_of, dt_time.max, tzinfo=timezone.utc)


def _extract_state_draft_overrides(
    state_draft: Any,
    snapshot_state_draft: Any,
    revision_raw_output: str,
) -> dict[str, Any]:
    if isinstance(state_draft, DraftRecommendation):
        return state_draft.model_dump()
    if isinstance(state_draft, Mapping):
        return dict(state_draft)
    if isinstance(state_draft, str) and state_draft.strip():
        try:
            parsed = json.loads(state_draft)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass
        if state_draft != snapshot_state_draft and state_draft != revision_raw_output:
            return {"memo": state_draft, "memo_claims": [state_draft]}
    return {}


def _build_unclamped_candidate_draft(
    *,
    base_response: AnalyzeResponse,
    synthesis: DecisionSynthesis,
    typed_result: DecisionMemoResult | None,
    raw_output: str,
    state_draft: Any,
    snapshot_state_draft: Any,
) -> tuple[DraftRecommendation, AnalyzeResponse]:
    overrides = _extract_state_draft_overrides(state_draft, snapshot_state_draft, raw_output)
    candidate = base_response.model_copy(deep=True)
    candidate.decision = cast(
        Literal["trade", "watchlist", "no_trade"],
        overrides.get("decision", synthesis.decision),
    )
    candidate.confidence = float(overrides.get("confidence", synthesis.confidence))
    if synthesis.reasons:
        candidate.reasons = list(synthesis.reasons)
    for field_name in _RISK_FIELDS:
        if field_name in overrides:
            value = overrides[field_name]
            if field_name in {"entry_range", "take_profit"} and isinstance(value, list):
                value = tuple(value)
            setattr(candidate, field_name, value)
        elif (proposed := getattr(synthesis, field_name)) is not None:
            setattr(candidate, field_name, proposed)

    memo_claims: list[str] = []
    if typed_result is not None:
        memo_resp = typed_result.response
        items = [*memo_resp.key_reasons, *memo_resp.key_risks, *memo_resp.what_would_change]
        memo_claims = [memo_resp.thesis, *(item.claim for item in items)]
    if "memo_claims" in overrides and isinstance(overrides["memo_claims"], list):
        memo_claims = [str(claim) for claim in overrides["memo_claims"]]
    memo_text = str(overrides.get("memo") or raw_output or "")

    draft_kwargs: dict[str, Any] = {
        "decision": candidate.decision,
        "confidence": candidate.confidence,
        "reasons": list(candidate.reasons),
        "warnings": list(candidate.warnings),
        "entry_range": candidate.entry_range,
        "stop_loss": candidate.stop_loss,
        "take_profit": candidate.take_profit,
        "risk_reward": candidate.risk_reward,
        "position_size_eur": candidate.position_size_eur,
        "memo": memo_text,
        "memo_claims": memo_claims,
        "source": "decision_synthesizer",
    }
    try:
        return DraftRecommendation(**draft_kwargs), candidate
    except ValidationError:
        return DraftRecommendation.model_construct(**draft_kwargs), candidate


def _is_blocking_post_revision_objection(objection: CritiqueObjection) -> bool:
    if objection.category == "risk_number_mismatch":
        return True
    return any(
        code.startswith(("decision_upgrade:", "missing_risk_field:", "memo_claim_mismatch:"))
        for code in objection.warning_codes
    )


def _capture_revision_snapshot(
    *,
    response: AnalyzeResponse,
    typed_results_len: int,
    draft_memo_text: str,
    session_state: Mapping[str, Any],
    pre_objections: Sequence[CritiqueObjection],
) -> _RevisionSnapshot:
    risk_fields = {
        field_name: getattr(response, field_name)
        for field_name in (*_RISK_FIELDS, "time_stop_at")
    }
    return _RevisionSnapshot(
        decision=response.decision,
        confidence=response.confidence,
        reasons=list(response.reasons),
        warnings=list(response.warnings),
        risk_fields=risk_fields,
        memo=response.memo,
        typed_results_len=typed_results_len,
        state_draft=session_state.get(DRAFT_RECOMMENDATION_OUTPUT_KEY, draft_memo_text),
        has_state_draft=DRAFT_RECOMMENDATION_OUTPUT_KEY in session_state,
        pre_objections=list(pre_objections),
    )


def _restore_snapshot(
    *,
    response: AnalyzeResponse,
    snapshot: _RevisionSnapshot,
    session_state: dict[str, Any],
) -> None:
    response.decision = snapshot.decision
    response.confidence = snapshot.confidence
    response.reasons = list(snapshot.reasons)
    for field_name, value in snapshot.risk_fields.items():
        setattr(response, field_name, value)
    response.memo = snapshot.memo
    if snapshot.has_state_draft:
        session_state[DRAFT_RECOMMENDATION_OUTPUT_KEY] = snapshot.state_draft


def _ensure_upgrade_objection(
    objections: list[CritiqueObjection],
    *,
    baseline_decision: str,
    candidate_decision: str,
) -> list[CritiqueObjection]:
    if any(
        any(code.startswith("decision_upgrade:") for code in obj.warning_codes)
        for obj in objections
    ):
        return objections
    return [
        *objections,
        CritiqueObjection(
            objection_id="det:decision_upgrade:post_revision",
            category="internal_inconsistency",
            severity="high",
            source="deterministic",
            claim=f"Revised decision '{candidate_decision}' upgrades '{baseline_decision}'.",
            warning_codes=[f"decision_upgrade:{baseline_decision}:{candidate_decision}"],
        ),
    ]


def _merge_effect_reason_codes(effect: CriticEffect, extra_codes: Sequence[str]) -> None:
    merged = list(dict.fromkeys([*effect.reason_codes, *extra_codes]))
    effect.reason_codes = merged


def _reject_revision_with_gate(
    *,
    runtime: BlackboardRuntime,
    response: AnalyzeResponse,
    snapshot: _RevisionSnapshot,
    baseline: DraftRecommendation,
    post_critique_request: CritiqueRequest,
    candidate_decision: str,
    post_objections: list[CritiqueObjection],
    blocking_objections: list[CritiqueObjection],
    risk_validation_error: str | None,
    is_upgrade: bool,
    effect: CriticEffect,
    session_state: dict[str, Any],
    warnings: list[str],
    revision_result: _DecisionRevisionResult,
    routing: RevisionRouting,
) -> BoundedRevisionOutcome:
    _restore_snapshot(response=response, snapshot=snapshot, session_state=session_state)
    warnings.append("critic_revision_failed")
    gate_objections = list(blocking_objections or post_objections)
    if is_upgrade:
        gate_objections = _ensure_upgrade_objection(
            gate_objections,
            baseline_decision=snapshot.decision,
            candidate_decision=candidate_decision,
        )
    gate_effect = evaluate_post_revision_policy(
        mode=runtime.current_critic_mode(),
        response=response.model_copy(deep=True),
        request=post_critique_request,
        baseline=baseline,
        deterministic_objections=gate_objections,
    )
    _merge_effect_reason_codes(
        effect,
        [*gate_effect.reason_codes, "critic_revision_rejected_deterministic"],
    )
    effect.deterministic_objection_ids = list(
        dict.fromkeys([*effect.deterministic_objection_ids, *(obj.objection_id for obj in gate_objections)])
    )
    detail = risk_validation_error or "; ".join(obj.claim for obj in gate_objections)
    return BoundedRevisionOutcome(
        revisions_performed=0,
        rejected=True,
        raw_output=revision_result.raw_output,
        latency_ms=revision_result.latency_ms,
        token_usage=dict(revision_result.token_usage),
        routing=routing,
        post_objections=post_objections,
        error_reason="critic_revision_failed",
        error_detail=detail or "post_revision_deterministic_gate_rejected",
        gate_effect=gate_effect,
    )


def _build_post_revision_critique_request(
    runtime: BlackboardRuntime,
    critique_kwargs: dict[str, Any],
    baseline: DraftRecommendation,
    candidate_draft: DraftRecommendation,
) -> CritiqueRequest:
    try:
        return runtime.build_critique_request_fn(**critique_kwargs)
    except ValidationError:
        safe_kwargs = {**critique_kwargs, "draft": baseline}
        return runtime.build_critique_request_fn(**safe_kwargs).model_copy(
            update={"draft": candidate_draft}
        )


def run_bounded_critic_revision(
    *,
    runtime: BlackboardRuntime,
    ticker: str,
    run_id: str,
    request: AnalyzeRequest,
    response: AnalyzeResponse,
    baseline: DraftRecommendation,
    critique_result: CritiqueResult,
    effect: CriticEffect,
    pre_revision_objections: Sequence[CritiqueObjection],
    fundamental_assessment: FundamentalAssessmentResult | None,
    technical_assessment: TechnicalAssessmentResult | None,
    fundamental_request: FundamentalAnalysisRequest | None,
    cross_domain_tension: CrossDomainTension | None,
    supplemental_evidence_chunks: Sequence[EvidenceChunkInput],
    effective_as_of: date | None,
    snapshot_at: datetime | None,
    typed_synthesis_request: DecisionSynthesisRequest | None,
    typed_results_len: int,
    draft_memo_text: str,
    session_state: dict[str, Any],
    warnings: list[str],
    typed_synthesis_obj: Any = None,
) -> BoundedRevisionOutcome:
    if MAX_CRITIC_REVISIONS != 1:
        raise ValueError("MAX_CRITIC_REVISIONS must remain 1 for the bounded critic loop.")

    routing = build_critic_revision_routing(critique_result, pre_revision_objections)
    snapshot = _capture_revision_snapshot(
        response=response,
        typed_results_len=typed_results_len,
        draft_memo_text=draft_memo_text,
        session_state=session_state,
        pre_objections=pre_revision_objections,
    )
    revision_result = runtime.request_synthesis_revision(
        ticker=ticker,
        request=request,
        response=response,
        baseline=baseline,
        revision_instructions=critique_result.response.revision_instructions,
        critic_findings=routing.critic_findings,
        critic_objections=routing.critic_objections,
        revision_objection_ids=routing.revision_objection_ids,
        revision_targets=routing.revision_targets,
        typed_synthesis=typed_synthesis_obj,
        typed_request=typed_synthesis_request,
    )
    if revision_result.synthesis is None:
        warnings.append("critic_revision_failed")
        return BoundedRevisionOutcome(
            revisions_performed=0,
            rejected=True,
            raw_output=revision_result.raw_output,
            latency_ms=revision_result.latency_ms,
            token_usage=dict(revision_result.token_usage),
            routing=routing,
            post_objections=list(pre_revision_objections),
            error_reason=revision_result.error_reason or "critic_revision_failed",
            error_detail=revision_result.error_detail,
        )

    risk_validation_error: str | None = None
    try:
        runtime.validate_risk_output_fn(revision_result.synthesis, response)
    except ValueError as exc:
        risk_validation_error = str(exc)

    candidate_draft, candidate_response = _build_unclamped_candidate_draft(
        base_response=response,
        synthesis=revision_result.synthesis,
        typed_result=revision_result.typed_result,
        raw_output=revision_result.raw_output,
        state_draft=session_state.get(DRAFT_RECOMMENDATION_OUTPUT_KEY),
        snapshot_state_draft=snapshot.state_draft,
    )
    critique_kwargs: dict[str, Any] = {
        "ticker": ticker,
        "run_id": run_id,
        "draft": candidate_draft,
        "analyst_views": build_analyst_views(
            fundamental=fundamental_assessment,
            technical=technical_assessment,
        ),
        "deterministic_warnings": response.warnings,
        "deterministic_diagnosis": response.diagnosis,
        "as_of": effective_as_of or request.as_of_date,
        "engine": runtime.engine,
        "fundamental_request": fundamental_request,
        "cross_domain_tension": cross_domain_tension,
        "supplemental_evidence_chunks": supplemental_evidence_chunks,
        "snapshot_at": snapshot_at,
    }
    embedder = runtime.embedding_client_provider() if runtime.embedding_client_provider else runtime.embedding_client
    if _can_embed(embedder):
        critique_kwargs["embedding_client"] = embedder
    post_critique_request = _build_post_revision_critique_request(
        runtime,
        critique_kwargs,
        baseline,
        candidate_draft,
    )
    post_objections = runtime.evaluate_deterministic_objections_fn(
        request=post_critique_request,
        baseline=baseline,
    )

    is_upgrade = _decision_rank(candidate_response.decision) > _decision_rank(snapshot.decision)
    blocking_objections = [
        obj for obj in post_objections if _is_blocking_post_revision_objection(obj)
    ]
    if risk_validation_error is not None or is_upgrade or blocking_objections:
        return _reject_revision_with_gate(
            runtime=runtime,
            response=response,
            snapshot=snapshot,
            baseline=baseline,
            post_critique_request=post_critique_request,
            candidate_decision=candidate_response.decision,
            post_objections=post_objections,
            blocking_objections=blocking_objections,
            risk_validation_error=risk_validation_error,
            is_upgrade=is_upgrade,
            effect=effect,
            session_state=session_state,
            warnings=warnings,
            revision_result=revision_result,
            routing=routing,
        )

    apply_downgrade_only_transition(
        response=response,
        proposed_decision=candidate_response.decision,
        proposed_confidence=candidate_response.confidence,
    )
    pre_keys = {(obj.objection_id, obj.category, obj.claim) for obj in pre_revision_objections}
    new_objections = [
        obj for obj in post_objections if (obj.objection_id, obj.category, obj.claim) not in pre_keys
    ]
    gate_effect: CriticEffect | None = None
    if new_objections:
        gate_effect = evaluate_post_revision_policy(
            mode=runtime.current_critic_mode(),
            response=response.model_copy(deep=True),
            request=post_critique_request,
            baseline=baseline,
            deterministic_objections=new_objections,
        )
        apply_downgrade_only_transition(
            response=response,
            proposed_decision=gate_effect.final_decision,
            proposed_confidence=gate_effect.final_confidence,
        )
        _merge_effect_reason_codes(effect, gate_effect.reason_codes)

    if revision_result.typed_result is not None and revision_result.typed_request is not None:
        response.memo = build_decision_memo(revision_result.typed_result.response, revision_result.typed_request)
        session_state[DRAFT_RECOMMENDATION_OUTPUT_KEY] = revision_result.typed_result.rendered_memo

    return BoundedRevisionOutcome(
        revisions_performed=1,
        rejected=False,
        raw_output=revision_result.raw_output,
        latency_ms=revision_result.latency_ms,
        token_usage=dict(revision_result.token_usage),
        routing=routing,
        post_objections=post_objections,
        typed_request=revision_result.typed_request,
        typed_result=revision_result.typed_result,
        gate_effect=gate_effect,
    )


class BlackboardFundamentalAgent(BaseAgent):
    """ADK BaseAgent executing FundamentalAnalyst and writing typed artifacts to session.state."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: SkipValidation[BlackboardRuntime]

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        seed_delta = self.runtime.ensure_initialized(ctx)
        merged_state = {**ctx.session.state, **seed_delta}
        mode = self.runtime.current_fundamental_mode()
        if mode == "off":
            payload = render_fundamental_view(None)
            yield Event(
                author=self.name,
                content=types.Content(role="model", parts=[types.Part(text=json.dumps(payload, sort_keys=True))]),
                actions=EventActions(state_delta={**seed_delta, FUNDAMENTAL_VIEW_OUTPUT_KEY: payload}),
            )
            return

        ticker = str(merged_state[TICKER_KEY])
        run_id = str(merged_state[RUN_ID_KEY])
        effective_as_of = date.fromisoformat(str(merged_state[EFFECTIVE_AS_OF_KEY]))

        def _execute() -> _FundamentalAssessmentOutcome:
            return _run_fundamental_outcome(
                self.runtime,
                mode=mode,
                ticker=ticker,
                run_id=run_id,
                as_of=effective_as_of,
            )

        if self.runtime.both_specialists_enabled():
            outcome: _FundamentalAssessmentOutcome = await _run_blocking_cancellable(_execute)
        else:
            outcome = _execute()

        view_payload = render_fundamental_view(outcome.assessment_result)
        delta: dict[str, Any] = {
            **seed_delta,
            FUNDAMENTAL_REQUEST_KEY: write_state_model(outcome.context),
            FUNDAMENTAL_ASSESSMENT_KEY: write_state_model(outcome.assessment_result),
            FUNDAMENTAL_CONTEXT_LATENCY_MS_KEY: outcome.context_latency_ms,
            FUNDAMENTAL_ASSESSMENT_LATENCY_MS_KEY: outcome.assessment_latency_ms,
            FUNDAMENTAL_VIEW_OUTPUT_KEY: view_payload,
        }
        yield Event(
            author=self.name,
            content=types.Content(
                role="model",
                parts=[types.Part(text=json.dumps(view_payload, sort_keys=True))],
            ),
            actions=EventActions(state_delta=delta),
        )


class BlackboardTechnicalAgent(BaseAgent):
    """ADK BaseAgent executing TechnicalAnalyst and writing typed artifacts to session.state."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: SkipValidation[BlackboardRuntime]

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        seed_delta = self.runtime.ensure_initialized(ctx)
        merged_state = {**ctx.session.state, **seed_delta}
        mode = self.runtime.current_technical_mode()
        if mode == "off":
            payload = render_technical_view(None)
            yield Event(
                author=self.name,
                content=types.Content(role="model", parts=[types.Part(text=json.dumps(payload, sort_keys=True))]),
                actions=EventActions(state_delta={**seed_delta, TECHNICAL_VIEW_OUTPUT_KEY: payload}),
            )
            return

        ticker = str(merged_state[TICKER_KEY])
        run_id = str(merged_state[RUN_ID_KEY])
        effective_as_of = date.fromisoformat(str(merged_state[EFFECTIVE_AS_OF_KEY]))

        def _execute() -> _TechnicalAssessmentOutcome:
            return _run_technical_outcome(
                self.runtime,
                mode=mode,
                ticker=ticker,
                run_id=run_id,
                as_of=effective_as_of,
            )

        if self.runtime.both_specialists_enabled():
            outcome: _TechnicalAssessmentOutcome = await _run_blocking_cancellable(_execute)
        else:
            outcome = _execute()

        view_payload = render_technical_view(outcome.assessment_result)
        delta: dict[str, Any] = {
            **seed_delta,
            TECHNICAL_REQUEST_KEY: write_state_model(outcome.context),
            TECHNICAL_ASSESSMENT_KEY: write_state_model(outcome.assessment_result),
            TECHNICAL_CONTEXT_LATENCY_MS_KEY: outcome.context_latency_ms,
            TECHNICAL_ASSESSMENT_LATENCY_MS_KEY: outcome.assessment_latency_ms,
            TECHNICAL_VIEW_OUTPUT_KEY: view_payload,
        }
        yield Event(
            author=self.name,
            content=types.Content(
                role="model",
                parts=[types.Part(text=json.dumps(view_payload, sort_keys=True))],
            ),
            actions=EventActions(state_delta=delta),
        )


class BlackboardPolicyFoldAgent(BaseAgent):
    """Joins specialist assessments and folds policies sequentially (Rules -> Fundamental -> Technical)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: SkipValidation[BlackboardRuntime]

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        seed_delta = self.runtime.ensure_initialized(ctx)
        state = {**ctx.session.state, **seed_delta}
        det_response = read_state_model(state, DETERMINISTIC_RESPONSE_KEY, AnalyzeResponse)
        response = read_state_model(state, ANALYSIS_RESPONSE_KEY, AnalyzeResponse)
        if det_response is None or response is None:
            raise ValueError("Blackboard deterministic response missing before policy fold.")

        effective_as_of = date.fromisoformat(str(state[EFFECTIVE_AS_OF_KEY]))
        fundamental_req = read_state_model(state, FUNDAMENTAL_REQUEST_KEY, FundamentalAnalysisRequest)
        fundamental_res = read_state_model(state, FUNDAMENTAL_ASSESSMENT_KEY, FundamentalAssessmentResult)
        technical_req = read_state_model(state, TECHNICAL_REQUEST_KEY, TechnicalAnalysisRequest)
        technical_res = read_state_model(state, TECHNICAL_ASSESSMENT_KEY, TechnicalAssessmentResult)

        delta: dict[str, Any] = {**seed_delta}
        fundamental_mode = self.runtime.current_fundamental_mode()
        if fundamental_mode != "off" and fundamental_req is not None and fundamental_res is not None:
            fund_effect = apply_fundamental_policy(
                mode=fundamental_mode,
                deterministic_response=response.model_copy(deep=True),
                request=fundamental_req,
                assessment_result=fundamental_res,
            )
            apply_downgrade_only_transition(
                response=response,
                proposed_decision=fund_effect.final_decision,
                proposed_confidence=response.confidence,
            )
            fund_effect.final_decision = response.decision
            fund_effect.decision_changed = fund_effect.final_decision != fund_effect.baseline_decision
            response.fundamental_agent_effect = fund_effect
            delta[FUNDAMENTAL_EFFECT_KEY] = write_state_model(fund_effect)

        technical_mode = self.runtime.current_technical_mode()
        if technical_mode != "off" and technical_req is not None and technical_res is not None:
            tech_effect = apply_technical_policy(
                mode=technical_mode,
                deterministic_response=det_response,
                post_fundamental_response=response.model_copy(deep=True),
                request=technical_req,
                assessment_result=technical_res,
            )
            apply_technical_effect_to_response(tech_effect, response)
            response.technical_agent_effect = tech_effect
            delta[TECHNICAL_EFFECT_KEY] = write_state_model(tech_effect)

        snapshot_at = (
            fundamental_req.snapshot_at
            if fundamental_req is not None
            else (
                technical_req.snapshot_at
                if technical_req is not None
                else datetime.combine(effective_as_of, dt_time.max, tzinfo=timezone.utc)
            )
        )
        delta[SNAPSHOT_AT_KEY] = snapshot_at.isoformat()
        delta[ANALYSIS_RESPONSE_KEY] = write_state_model(response)
        delta[BASELINE_DRAFT_KEY] = write_state_model(
            build_draft_from_response(response, source="deterministic")
        )
        yield Event(
            author=self.name,
            content=types.Content(
                role="model",
                parts=[types.Part(text=json.dumps({"decision": response.decision}, sort_keys=True))],
            ),
            actions=EventActions(state_delta=delta),
        )


class CrossDomainTensionAgent(BaseAgent):
    """Detects CrossDomainTension and runs at most one bounded supplemental pgvector lookup."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: SkipValidation[BlackboardRuntime]

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        ticker = str(state[TICKER_KEY])
        effective_as_of = date.fromisoformat(str(state[EFFECTIVE_AS_OF_KEY]))
        snapshot_at = _resolve_snapshot_at_from_state(state, effective_as_of)
        fundamental_req = read_state_model(state, FUNDAMENTAL_REQUEST_KEY, FundamentalAnalysisRequest)
        fundamental_res = read_state_model(state, FUNDAMENTAL_ASSESSMENT_KEY, FundamentalAssessmentResult)
        technical_req = read_state_model(state, TECHNICAL_REQUEST_KEY, TechnicalAnalysisRequest)
        technical_res = read_state_model(state, TECHNICAL_ASSESSMENT_KEY, TechnicalAssessmentResult)

        started_at = time.monotonic()
        embedder = self.runtime.resolve_embedding_client()
        resolution = self.runtime.resolve_tension_fn(
            ticker=ticker,
            snapshot_at=snapshot_at,
            fundamental_assessment=fundamental_res,
            technical_assessment=technical_res,
            fundamental_request=fundamental_req,
            technical_request=technical_req,
            embedding_client=embedder,
            engine=self.runtime.engine,
        )
        latency_ms = (time.monotonic() - started_at) * 1000
        delta: dict[str, Any] = {
            CROSS_DOMAIN_TENSION_KEY: (
                write_state_model(resolution.tension) if resolution.tension is not None else None
            ),
            SUPPLEMENTAL_EVIDENCE_CHUNKS_KEY: write_state_models(resolution.supplemental_chunks),
            TENSION_LATENCY_MS_KEY: latency_ms,
        }
        yield Event(
            author=self.name,
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=json.dumps(
                            {"has_tension": resolution.tension is not None},
                            sort_keys=True,
                        )
                    )
                ],
            ),
            actions=EventActions(state_delta=delta),
        )


class BlackboardSynthesizerAgent(BaseAgent):
    """Executes typed DecisionSynthesizer on session.state and writes memo artifacts."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: SkipValidation[BlackboardRuntime]

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        response = read_state_model(state, ANALYSIS_RESPONSE_KEY, AnalyzeResponse)
        if response is None:
            raise ValueError("Blackboard analysis_response missing before synthesis.")
        run_id = str(state[RUN_ID_KEY])
        effective_as_of = date.fromisoformat(str(state[EFFECTIVE_AS_OF_KEY]))
        snapshot_at = _resolve_snapshot_at_from_state(state, effective_as_of)
        fundamental_res = read_state_model(state, FUNDAMENTAL_ASSESSMENT_KEY, FundamentalAssessmentResult)
        technical_res = read_state_model(state, TECHNICAL_ASSESSMENT_KEY, TechnicalAssessmentResult)
        tension = read_state_model(state, CROSS_DOMAIN_TENSION_KEY, CrossDomainTension)
        supplemental_chunks = read_state_models(state, SUPPLEMENTAL_EVIDENCE_CHUNKS_KEY, EvidenceChunkInput)

        synthesis_request = build_synthesis_request(
            run_id=run_id,
            response=response,
            analyst_views=build_analyst_views(
                fundamental=fundamental_res,
                technical=technical_res,
            ),
            as_of_date=effective_as_of,
            snapshot_at=snapshot_at,
            cross_domain_tension=tension,
            supplemental_evidence_chunks=supplemental_chunks,
        )
        result = self.runtime.get_synthesizer().synthesize(synthesis_request)
        apply_downgrade_only_transition(
            response=response,
            proposed_decision=result.response.proposed_decision,
            proposed_confidence=result.response.confidence,
        )
        memo = build_decision_memo(result.response, synthesis_request)
        response.memo = memo
        synth_effect = SynthesizerEffect(
            contract="typed",
            prompt_version=result.prompt_version,
            fallback_used=result.used_fallback,
            proposed_decision=result.response.proposed_decision,
            final_decision=response.decision,
            validation_failures=list(dict.fromkeys(result.validation.reason_codes)),
            memo_hash=memo_hash(memo),
        )
        delta: dict[str, Any] = {
            DECISION_SYNTHESIS_REQUEST_KEY: write_state_model(synthesis_request),
            DECISION_MEMO_RESULT_KEY: write_state_model(result),
            SYNTHESIZER_EFFECT_KEY: write_state_model(synth_effect),
            DRAFT_RECOMMENDATION_OUTPUT_KEY: result.rendered_memo,
            ANALYSIS_RESPONSE_KEY: write_state_model(response),
        }
        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=result.rendered_memo)]),
            actions=EventActions(state_delta=delta),
        )


class BlackboardCriticAgent(BaseAgent):
    """Single-call typed Critic node with routed MAX_CRITIC_REVISIONS=1 and post-revision gate."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: SkipValidation[BlackboardRuntime]

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = dict(ctx.session.state)
        mode = self.runtime.current_critic_mode()
        if mode == "off":
            payload = {"status": "skipped", "reason": "critic_mode_off"}
            yield Event(
                author=self.name,
                content=types.Content(role="model", parts=[types.Part(text=json.dumps(payload, sort_keys=True))]),
                actions=EventActions(state_delta={CRITIQUE_OUTPUT_KEY: payload, CRITIC_REVISION_COUNT_KEY: 0}),
            )
            return

        response, baseline = _require_critic_state(state)
        warnings = list(state.get(WORKFLOW_WARNINGS_KEY, []))
        draft_text = str(state.get(DRAFT_RECOMMENDATION_OUTPUT_KEY) or "")
        memo_result = read_state_model(state, DECISION_MEMO_RESULT_KEY, DecisionMemoResult)
        if memo_result is None and draft_text:
            _fold_legacy_draft_into_response(self.runtime, response, draft_text, warnings)

        critique_request = self._build_critique_request(state, response, memo_result, draft_text)
        deterministic_objections = self.runtime.evaluate_deterministic_objections_fn(
            request=critique_request,
            baseline=baseline,
        )
        critique_result = self.runtime.review_critique(critique_request)
        effect = apply_critic_policy(
            mode=mode,
            response=response.model_copy(deep=True),
            request=critique_request,
            baseline=baseline,
            critique_result=critique_result,
            deterministic_objections=deterministic_objections,
        )
        synth_request, memo_result, synth_effect, revision_outcome = self._maybe_run_enforced_revision(
            mode=mode,
            state=state,
            response=response,
            baseline=baseline,
            critique_result=critique_result,
            effect=effect,
            deterministic_objections=deterministic_objections,
            memo_result=memo_result,
            draft_text=draft_text,
            warnings=warnings,
        )
        response.critic_effect = effect
        delta = self._build_critic_delta(
            state=state,
            response=response,
            critique_request=critique_request,
            critique_result=critique_result,
            effect=effect,
            deterministic_objections=deterministic_objections,
            revision_outcome=revision_outcome,
            synth_request=synth_request,
            memo_result=memo_result,
            synth_effect=synth_effect,
            warnings=warnings,
        )
        yield Event(
            author=self.name,
            content=types.Content(
                role="model",
                parts=[types.Part(text=json.dumps(delta[CRITIQUE_OUTPUT_KEY], sort_keys=True))],
            ),
            actions=EventActions(state_delta=delta),
        )

    def _build_critique_request(
        self,
        state: Mapping[str, Any],
        response: AnalyzeResponse,
        memo_result: DecisionMemoResult | None,
        draft_text: str,
    ) -> CritiqueRequest:
        effective_as_of = date.fromisoformat(str(state[EFFECTIVE_AS_OF_KEY]))
        critique_kwargs: dict[str, Any] = {
            "ticker": str(state[TICKER_KEY]),
            "run_id": str(state[RUN_ID_KEY]),
            "draft": build_draft_from_response(
                response,
                memo=draft_text or (memo_result.rendered_memo if memo_result is not None else ""),
                memo_claims=_extract_memo_claims(memo_result),
                source="decision_synthesizer",
            ),
            "analyst_views": build_analyst_views(
                fundamental=read_state_model(state, FUNDAMENTAL_ASSESSMENT_KEY, FundamentalAssessmentResult),
                technical=read_state_model(state, TECHNICAL_ASSESSMENT_KEY, TechnicalAssessmentResult),
            ),
            "deterministic_warnings": response.warnings,
            "deterministic_diagnosis": response.diagnosis,
            "as_of": effective_as_of,
            "engine": self.runtime.engine,
            "fundamental_request": read_state_model(state, FUNDAMENTAL_REQUEST_KEY, FundamentalAnalysisRequest),
            "cross_domain_tension": read_state_model(state, CROSS_DOMAIN_TENSION_KEY, CrossDomainTension),
            "supplemental_evidence_chunks": read_state_models(
                state,
                SUPPLEMENTAL_EVIDENCE_CHUNKS_KEY,
                EvidenceChunkInput,
            ),
            "snapshot_at": _resolve_snapshot_at_from_state(state, effective_as_of),
        }
        embedder = self.runtime.resolve_embedding_client()
        if _can_embed(embedder):
            critique_kwargs["embedding_client"] = embedder
        return self.runtime.build_critique_request_fn(**critique_kwargs)

    def _maybe_run_enforced_revision(
        self,
        *,
        mode: Literal["off", "shadow", "enforced"],
        state: dict[str, Any],
        response: AnalyzeResponse,
        baseline: DraftRecommendation,
        critique_result: CritiqueResult,
        effect: CriticEffect,
        deterministic_objections: list[CritiqueObjection],
        memo_result: DecisionMemoResult | None,
        draft_text: str,
        warnings: list[str],
    ) -> tuple[
        DecisionSynthesisRequest | None,
        DecisionMemoResult | None,
        SynthesizerEffect | None,
        BoundedRevisionOutcome | None,
    ]:
        synth_request = read_state_model(state, DECISION_SYNTHESIS_REQUEST_KEY, DecisionSynthesisRequest)
        synth_effect = read_state_model(state, SYNTHESIZER_EFFECT_KEY, SynthesizerEffect)
        if mode != "enforced":
            return synth_request, memo_result, synth_effect, None
        if effect.decision_changed or effect.final_confidence != response.confidence:
            apply_downgrade_only_transition(
                response=response,
                proposed_decision=effect.final_decision,
                proposed_confidence=effect.final_confidence,
            )
        if effect.revisions_requested != 1:
            return synth_request, memo_result, synth_effect, None

        effective_as_of = date.fromisoformat(str(state[EFFECTIVE_AS_OF_KEY]))
        revision_outcome = run_bounded_critic_revision(
            runtime=self.runtime,
            ticker=str(state[TICKER_KEY]),
            run_id=str(state[RUN_ID_KEY]),
            request=AnalyzeRequest(ticker=str(state[TICKER_KEY]), as_of_date=effective_as_of),
            response=response,
            baseline=baseline,
            critique_result=critique_result,
            effect=effect,
            pre_revision_objections=deterministic_objections,
            fundamental_assessment=read_state_model(state, FUNDAMENTAL_ASSESSMENT_KEY, FundamentalAssessmentResult),
            technical_assessment=read_state_model(state, TECHNICAL_ASSESSMENT_KEY, TechnicalAssessmentResult),
            fundamental_request=read_state_model(state, FUNDAMENTAL_REQUEST_KEY, FundamentalAnalysisRequest),
            cross_domain_tension=read_state_model(state, CROSS_DOMAIN_TENSION_KEY, CrossDomainTension),
            supplemental_evidence_chunks=read_state_models(
                state,
                SUPPLEMENTAL_EVIDENCE_CHUNKS_KEY,
                EvidenceChunkInput,
            ),
            effective_as_of=effective_as_of,
            snapshot_at=_resolve_snapshot_at_from_state(state, effective_as_of),
            typed_synthesis_request=synth_request,
            typed_results_len=1 if memo_result is not None else 0,
            draft_memo_text=draft_text,
            session_state=state,
            warnings=warnings,
        )
        effect.revisions_performed = revision_outcome.revisions_performed
        effect.final_decision = response.decision
        effect.final_confidence = response.confidence
        effect.decision_changed = effect.final_decision != effect.baseline_decision
        if (
            revision_outcome.typed_result is not None
            and revision_outcome.typed_request is not None
            and not revision_outcome.rejected
        ):
            synth_request = revision_outcome.typed_request
            memo_result = revision_outcome.typed_result
            synth_effect = _updated_synthesizer_effect(synth_effect, memo_result, response)
        return synth_request, memo_result, synth_effect, revision_outcome

    @staticmethod
    def _build_critic_delta(
        *,
        state: Mapping[str, Any],
        response: AnalyzeResponse,
        critique_request: CritiqueRequest,
        critique_result: CritiqueResult,
        effect: CriticEffect,
        deterministic_objections: Sequence[CritiqueObjection],
        revision_outcome: BoundedRevisionOutcome | None,
        synth_request: DecisionSynthesisRequest | None,
        memo_result: DecisionMemoResult | None,
        synth_effect: SynthesizerEffect | None,
        warnings: list[str],
    ) -> dict[str, Any]:
        all_objections = [*deterministic_objections, *critique_result.response.objections]
        critique_payload: dict[str, Any] = {
            "source": "typed_critic_view",
            "adk_token_usage": {},
            "status": critique_result.response.status,
            "verdict": critique_result.response.verdict,
            "objection_counts_by_severity": _objection_counts_by_severity(all_objections),
            "objections": _serialize_critic_objections(all_objections),
            "baseline_decision": effect.baseline_decision,
            "final_decision": effect.final_decision,
            "accepted": critique_result.validation.accepted,
            "reason_codes": list(effect.reason_codes),
        }
        if revision_outcome is not None and (revision_outcome.revisions_performed == 1 or revision_outcome.rejected):
            critique_payload["revision_objection_ids"] = list(revision_outcome.routing.revision_objection_ids)
            critique_payload["revision_targets"] = [
                target.model_dump(mode="json") for target in revision_outcome.routing.revision_targets
            ]
        step_meta = _build_critic_step_meta(
            critique_result=critique_result,
            revision_outcome=revision_outcome,
            critique_payload=critique_payload,
        )
        delta: dict[str, Any] = {
            CRITIQUE_REQUEST_KEY: write_state_model(critique_request),
            CRITIQUE_RESULT_KEY: write_state_model(critique_result),
            CRITIC_EFFECT_KEY: write_state_model(effect),
            DETERMINISTIC_OBJECTIONS_KEY: write_state_models(deterministic_objections),
            POST_REVISION_OBJECTIONS_KEY: write_state_models(
                revision_outcome.post_objections if revision_outcome is not None else deterministic_objections
            ),
            CRITIC_REVISION_COUNT_KEY: effect.revisions_performed,
            CRITIQUE_OUTPUT_KEY: critique_payload,
            ANALYSIS_RESPONSE_KEY: write_state_model(response),
            WORKFLOW_WARNINGS_KEY: warnings,
            CRITIC_STEP_META_KEY: step_meta,
        }
        if DRAFT_RECOMMENDATION_OUTPUT_KEY in state:
            delta[DRAFT_RECOMMENDATION_OUTPUT_KEY] = state[DRAFT_RECOMMENDATION_OUTPUT_KEY]
        if synth_request is not None:
            delta[DECISION_SYNTHESIS_REQUEST_KEY] = write_state_model(synth_request)
        if memo_result is not None:
            delta[DECISION_MEMO_RESULT_KEY] = write_state_model(memo_result)
        if synth_effect is not None:
            delta[SYNTHESIZER_EFFECT_KEY] = write_state_model(synth_effect)
        return delta


def _require_critic_state(state: Mapping[str, Any]) -> tuple[AnalyzeResponse, DraftRecommendation]:
    response = read_state_model(state, ANALYSIS_RESPONSE_KEY, AnalyzeResponse)
    if response is None:
        raise ValueError("Blackboard response missing before critic.")
    baseline = read_state_model(state, BASELINE_DRAFT_KEY, DraftRecommendation)
    if baseline is None:
        baseline = build_draft_from_response(response, source="deterministic")
    return response, baseline


def _extract_memo_claims(memo_result: DecisionMemoResult | None) -> list[str]:
    if memo_result is None:
        return []
    memo_resp = memo_result.response
    items = [*memo_resp.key_reasons, *memo_resp.key_risks, *memo_resp.what_would_change]
    return [memo_resp.thesis, *(item.claim for item in items)]


def _updated_synthesizer_effect(
    synth_effect: SynthesizerEffect | None,
    memo_result: DecisionMemoResult,
    response: AnalyzeResponse,
) -> SynthesizerEffect | None:
    if synth_effect is None or response.memo is None:
        return synth_effect
    all_failures = [*synth_effect.validation_failures, *memo_result.validation.reason_codes]
    return SynthesizerEffect(
        contract="typed",
        prompt_version=memo_result.prompt_version,
        fallback_used=synth_effect.fallback_used or memo_result.used_fallback,
        proposed_decision=synth_effect.proposed_decision,
        final_decision=response.decision,
        validation_failures=list(dict.fromkeys(all_failures)),
        memo_hash=memo_hash(response.memo),
    )


def _fold_legacy_draft_into_response(
    runtime: BlackboardRuntime,
    response: AnalyzeResponse,
    draft_text: str,
    warnings: list[str],
) -> None:
    synthesis = parse_legacy_synthesis(draft_text)
    if synthesis is None:
        return
    try:
        runtime.validate_risk_output_fn(synthesis, response)
    except ValueError as exc:
        warnings.append(f"guardrail_rejected: {exc}")
        return
    apply_downgrade_only_transition(
        response=response,
        proposed_decision=synthesis.decision,
        proposed_confidence=synthesis.confidence,
    )
    for field_name in _RISK_FIELDS:
        proposed = getattr(synthesis, field_name)
        if proposed is not None:
            setattr(response, field_name, proposed)


def _build_critic_step_meta(
    *,
    critique_result: CritiqueResult,
    revision_outcome: BoundedRevisionOutcome | None,
    critique_payload: dict[str, Any],
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "critique_latency_ms": critique_result.latency_ms,
        "critique_token_usage": dict(critique_result.token_usage),
        "critique_accepted": critique_result.validation.accepted,
        "critique_diagnostics": [
            *critique_result.validation.reason_codes,
            *critique_result.validation_diagnostics,
        ],
        "output": critique_payload,
        "revisions_performed": 0,
        "revision_rejected": False,
        "revision_token_usage": {},
        "revision_latency_ms": 0.0,
        "revision_raw_output": "",
        "revision_error_reason": None,
        "revision_error_detail": None,
        "revision_objection_ids": [],
        "revision_targets": [],
    }
    if revision_outcome is not None:
        meta.update(
            {
                "revisions_performed": revision_outcome.revisions_performed,
                "revision_rejected": revision_outcome.rejected,
                "revision_token_usage": dict(revision_outcome.token_usage),
                "revision_latency_ms": revision_outcome.latency_ms,
                "revision_raw_output": revision_outcome.raw_output,
                "revision_error_reason": revision_outcome.error_reason,
                "revision_error_detail": revision_outcome.error_detail,
                "revision_objection_ids": list(revision_outcome.routing.revision_objection_ids),
                "revision_targets": [
                    target.model_dump(mode="json")
                    for target in revision_outcome.routing.revision_targets
                ],
            }
        )
    return meta
