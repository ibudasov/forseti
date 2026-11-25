from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlmodel import Session, select

import agents.orchestration.workflow as workflow_module
import app.services.fundamental_context as fundamental_context_module
from agents.config import load_agent_config
from agents.orchestration.registry import (
    CRITIC_NAME,
    DECISION_SYNTHESIZER_NAME,
    FUNDAMENTAL_ANALYST_NAME,
    SPECIALIST_ORDER,
    TECHNICAL_ANALYST_NAME,
)
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.db.models import DocumentChunk, SourceQualityTier, SourceType
from app.db.repository import upsert_document_chunks
from app.rag.embedding import MockEmbeddingClient
from app.rag.ingestion.base import compute_source_hash
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from app.schemas.critique import CritiqueObjection, CritiqueResponse, CritiqueResult, CritiqueValidation
from app.schemas.fundamentals import (
    CitedFinding,
    FundamentalAssessmentResponse,
    FundamentalAssessmentResult,
    FundamentalAssessmentValidation,
)
from app.schemas.technicals import (
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
    TechnicalAssessmentValidation,
    TechnicalFinding,
)
from app.services.decision_synthesizer import DecisionSynthesizer, ModelOutput
from app.services.fundamental_context import build_fundamental_analysis_request
from app.settings import Settings
from app.trust.invariants import (
    CRITIC_OFF_ALLOWED_SKIPS,
    DECISION_RANK,
    RISK_FIELDS,
    evaluate_agentic_invariants,
)
from tests.agents.test_parallel_specialists import _seed_full_security

AS_OF_DATE = date(2026, 9, 8)
SNAPSHOT_AT = datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc)


class DeterministicEmbeddingClient:
    """Offline deterministic 768-dim unit-vector embedder keyed by text hash."""

    def __init__(self, dimension: int = 768) -> None:
        self._dimension = dimension
        self.queries: list[str] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            self.queries.append(text)
            vectors.append(_deterministic_vector(text, self._dimension))
        return vectors


def _deterministic_vector(text: str, dimension: int = 768) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [((digest[i % len(digest)] / 255.0) * 2.0) - 1.0 for i in range(dimension)]
    norm = math.sqrt(sum(val * val for val in raw)) or 1.0
    return [val / norm for val in raw]


def _configure_modes(
    monkeypatch,
    *,
    fundamental_mode: str = "enforced",
    technical_mode: str = "enforced",
    critic_mode: str = "enforced",
    synthesizer_contract: str = "typed",
) -> Settings:
    settings = Settings(
        _env_file=None,
        PIPELINE_MODE="agentic",
        FUNDAMENTAL_AGENT_MODE=fundamental_mode,
        TECHNICAL_AGENT_MODE=technical_mode,
        CRITIC_MODE=critic_mode,
        SYNTHESIZER_CONTRACT=synthesizer_contract,
        AGENT_MAX_RETRIES=0,
    )
    monkeypatch.setattr(workflow_module, "get_settings", lambda: settings)
    monkeypatch.setattr("app.services.decision_synthesizer.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.critic.get_settings", lambda: settings)
    return settings


def _trade_baseline(ticker: str = "TENS") -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker=ticker,
        decision="trade",
        confidence=0.80,
        reasons=["Strong deterministic checklist"],
        warnings=[],
        entry_range=(100.0, 102.0),
        stop_loss=94.0,
        take_profit=(112.0, 118.0),
        risk_reward=2.5,
        position_size_eur=1200.0,
        time_stop_at=date(2026, 9, 28),
        engine_version="v1.rules.0",
        trace_id="baseline",
    )


def _positive_fundamental_assessment(request) -> FundamentalAssessmentResult:
    chunk_ids = [chunk.chunk_id for chunk in request.evidence_chunks[:1]]
    return FundamentalAssessmentResult(
        response=FundamentalAssessmentResponse(
            run_id=request.run_id,
            context_hash=request.context_hash,
            status="completed",
            overall_signal="positive",
            proposed_score_adjustment=0,
            findings=[
                CitedFinding(
                    finding_id="fund-1",
                    category="growth_quality",
                    direction="positive",
                    materiality="medium",
                    claim="Revenue growth remains strong.",
                    metric_ids=[],
                    chunk_ids=chunk_ids,
                )
            ]
            if chunk_ids
            else [],
            evidence_coverage=1.0,
            summary="Fundamentals are positive.",
        ),
        validation=FundamentalAssessmentValidation(accepted=True, reason_codes=[]),
        model_name="offline-fund",
        prompt_version="1.0",
    )


def _conflicting_technical_assessment(request, *, signal: Any = "negative") -> TechnicalAssessmentResult:
    metric_id = request.facts[0].metric_id if request.facts else f"close:{AS_OF_DATE.isoformat()}"
    return TechnicalAssessmentResult(
        response=TechnicalAssessmentResponse(
            run_id=request.run_id,
            context_hash=request.context_hash,
            status="completed",
            overall_signal=signal,
            trend_state="downtrend" if signal != "positive" else "uptrend",
            momentum_state="weakening" if signal != "positive" else "overextended",
            proposed_score_adjustment=0,
            findings=[
                TechnicalFinding(
                    finding_id="tech-1",
                    category="momentum",
                    direction="negative" if signal != "positive" else "positive",
                    materiality="medium",
                    claim="Technical momentum signal.",
                    metric_ids=[metric_id],
                )
            ],
            risk_flags=[
                TechnicalFinding(
                    finding_id="tech-risk-1",
                    category="event_risk",
                    direction="negative",
                    materiality="high",
                    claim="Earnings catalyst risk within 10 days.",
                    metric_ids=[metric_id],
                )
            ],
            summary="Technical risk flagged.",
        ),
        validation=TechnicalAssessmentValidation(accepted=True, reason_codes=[]),
        model_name="offline-tech",
        prompt_version="1.0",
    )


def _aligned_technical_assessment(request) -> TechnicalAssessmentResult:
    metric_id = request.facts[0].metric_id if request.facts else f"close:{AS_OF_DATE.isoformat()}"
    return TechnicalAssessmentResult(
        response=TechnicalAssessmentResponse(
            run_id=request.run_id,
            context_hash=request.context_hash,
            status="completed",
            overall_signal="positive",
            trend_state="uptrend",
            momentum_state="strengthening",
            proposed_score_adjustment=0,
            findings=[
                TechnicalFinding(
                    finding_id="tech-aligned",
                    category="trend",
                    direction="positive",
                    materiality="medium",
                    claim="Uptrend confirmed.",
                    metric_ids=[metric_id],
                )
            ],
            risk_flags=[],
            summary="Technical view aligned positive.",
        ),
        validation=TechnicalAssessmentValidation(accepted=True, reason_codes=[]),
        model_name="offline-tech",
        prompt_version="1.0",
    )


def _adk_events():
    return [
        SimpleNamespace(author=FUNDAMENTAL_ANALYST_NAME, content=SimpleNamespace(parts=[]), usage_metadata=None),
        SimpleNamespace(author=TECHNICAL_ANALYST_NAME, content=SimpleNamespace(parts=[]), usage_metadata=None),
        SimpleNamespace(author=DECISION_SYNTHESIZER_NAME, content=SimpleNamespace(parts=[]), usage_metadata=None),
        SimpleNamespace(author=CRITIC_NAME, content=SimpleNamespace(parts=[]), usage_metadata=None),
    ]


def test_workflow_pgvector_tension_lookup_dedup_pit_and_revision_propagation(
    pgvector_engine,
    monkeypatch,
):
    ticker = "TENS"
    _seed_full_security(pgvector_engine, ticker=ticker, as_of_date=AS_OF_DATE)

    settings = _configure_modes(monkeypatch)
    embedder = DeterministicEmbeddingClient()
    expected_query = (
        f"{ticker} polarity conflict fundamental positive technical negative "
        "downtrend event_risk growth_quality momentum"
    )
    query_vec = _deterministic_vector(expected_query)
    growth_vec = _deterministic_vector("What explains recent revenue growth or decline, and is it sustainable?")

    chunk_a = DocumentChunk(
        ticker=ticker,
        source_type=SourceType.filing_mda,
        document_id=f"doc:{ticker}:a",
        source_url="https://example.com/a",
        publisher="SEC",
        title="Chunk A",
        source_quality_tier=SourceQualityTier.primary_regulatory,
        source_hash=compute_source_hash("https://example.com/a", 0, "Growth was 25% YoY."),
        published_at=SNAPSHOT_AT - timedelta(days=10),
        ingested_at=SNAPSHOT_AT,
        chunk_index=0,
        text="Growth was 25% YoY.",
        embedding=growth_vec,
    )
    chunk_b = DocumentChunk(
        ticker=ticker,
        source_type=SourceType.filing_business,
        document_id=f"doc:{ticker}:b",
        source_url="https://example.com/b",
        publisher="SEC",
        title="Chunk B",
        source_quality_tier=SourceQualityTier.primary_regulatory,
        source_hash=compute_source_hash("https://example.com/b", 1, "Event risk margin squeeze 18%."),
        published_at=SNAPSHOT_AT - timedelta(days=5),
        ingested_at=SNAPSHOT_AT,
        chunk_index=1,
        text="Event risk margin squeeze 18%.",
        embedding=query_vec,
    )
    chunk_c_future = DocumentChunk(
        ticker=ticker,
        source_type=SourceType.company_news,
        document_id=f"doc:{ticker}:c",
        source_url="https://example.com/c",
        publisher="News",
        title="Chunk C Future",
        source_quality_tier=SourceQualityTier.secondary_reputable,
        source_hash=compute_source_hash("https://example.com/c", 2, "Future leak 99% drop."),
        published_at=SNAPSHOT_AT + timedelta(days=1),
        ingested_at=SNAPSHOT_AT,
        chunk_index=2,
        text="Future leak 99% drop.",
        embedding=query_vec,
    )
    upsert_document_chunks([chunk_a, chunk_b, chunk_c_future], engine=pgvector_engine)

    with Session(pgvector_engine) as session:
        rows = session.exec(select(DocumentChunk).where(DocumentChunk.ticker == ticker)).all()
    id_by_title = {row.title: row.id for row in rows}
    chunk_a_id = id_by_title["Chunk A"]
    chunk_b_id = id_by_title["Chunk B"]
    chunk_c_id = id_by_title["Chunk C Future"]

    baseline = _trade_baseline(ticker)
    monkeypatch.setattr(
        workflow_module,
        "resolve_ticker",
        lambda ref: SimpleNamespace(is_valid=True, ticker=ticker, error=None),
    )
    monkeypatch.setattr(workflow_module, "analyze_request", lambda req, engine=None: baseline.model_copy(deep=True))

    fund_build_calls = 0
    original_build_fundamental = build_fundamental_analysis_request

    def counted_build_fundamental(*args, **kwargs):
        nonlocal fund_build_calls
        fund_build_calls += 1
        return original_build_fundamental(*args, **kwargs)

    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", counted_build_fundamental)
    monkeypatch.setattr("app.services.critique_context.build_fundamental_analysis_request", counted_build_fundamental)
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(assess=_positive_fundamental_assessment),
    )
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(assess=_conflicting_technical_assessment),
    )

    synth_requests = []

    class _CapturingSynthesizerModel:
        model_name = "capturing-synth"

        def generate(self, prompt: str) -> ModelOutput:
            payload = json.loads(prompt.split("DecisionSynthesisRequest:\n", 1)[1])
            synth_requests.append(payload)
            return ModelOutput(
                text=json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": payload["run_id"],
                        "context_hash": payload["context_hash"],
                        "proposed_decision": "watchlist",
                        "confidence": 0.60,
                        "thesis": "Cross-domain tension justifies watchlist.",
                        "key_reasons": [
                            {
                                "claim": "Event risk margin squeeze 18%.",
                                "metric_ids": [],
                                "chunk_ids": [chunk_b_id],
                                "warning_codes": [],
                            }
                        ],
                        "key_risks": [],
                        "what_would_change": [],
                    }
                ),
                token_usage={"total_token_count": 7},
            )

    captured_critique_requests = []

    def _revise_critic(self, critique_request):
        del self
        captured_critique_requests.append(critique_request)
        return CritiqueResult(
            response=CritiqueResponse(
                run_id=critique_request.run_id,
                context_hash=critique_request.context_hash,
                status="completed",
                verdict="revise",
                objections=[
                    CritiqueObjection(
                        objection_id="model:tension:1",
                        category="analyst_contradiction",
                        severity="medium",
                        source="model",
                        claim="Highlight supplemental chunk risk.",
                        chunk_ids=[chunk_b_id],
                    )
                ],
                proposed_confidence_penalty=0.05,
                revision_instructions="Reiterate chunk B tension.",
                summary="Revise to emphasize tension.",
            ),
            validation=CritiqueValidation(accepted=True),
            model_name="offline-critic",
            prompt_version="critic-guardrail.v1",
        )

    monkeypatch.setattr(AgenticAnalysisWorkflow, "_review_critique", _revise_critic)

    workflow = AgenticAnalysisWorkflow(
        load_agent_config(settings),
        engine=pgvector_engine,
        embedding_client=embedder,
        decision_synthesizer=DecisionSynthesizer(model_port=_CapturingSynthesizerModel(), max_retries=0),
        runner_factory=lambda registry, sym: iter(_adk_events()),
    )

    response = workflow.analyze(ticker, request=AnalyzeRequest(ticker=ticker, as_of_date=AS_OF_DATE))

    # 1. No redundant fundamental context rebuild inside build_critique_request
    assert fund_build_calls == 1
    # 2. Initial synthesis + 1 Critic revision both received CrossDomainTension & supplemental chunk B (no future chunk C)
    assert len(synth_requests) == 2
    for synth_req in synth_requests:
        assert synth_req["cross_domain_tension"] is not None
        assert chunk_b_id in synth_req["known_chunk_ids"]
        assert chunk_c_id not in synth_req["known_chunk_ids"]
        supp_ids = [chunk["chunk_id"] for chunk in synth_req["supplemental_evidence_chunks"]]
        assert chunk_b_id in supp_ids
        assert chunk_a_id not in supp_ids
        assert chunk_c_id not in supp_ids

    # 3. CritiqueRequest reused fundamental chunks + merged deduplicated supplemental chunk B (never chunk C)
    assert len(captured_critique_requests) == 1
    crit_req = captured_critique_requests[0]
    assert crit_req.cross_domain_tension is not None
    crit_chunk_ids = [chunk.chunk_id for chunk in crit_req.evidence_chunks]
    assert len(crit_chunk_ids) == len(set(crit_chunk_ids))
    assert chunk_b_id in crit_chunk_ids
    assert chunk_c_id not in crit_chunk_ids

    # 4. Trace step + invariants + ADR 002/008
    assert response.trace is not None
    step_names = [step.agent_name for step in response.trace.steps]
    assert "cross_domain_tension" in step_names
    tension_step = next(step for step in response.trace.steps if step.agent_name == "cross_domain_tension")
    assert tension_step.token_usage == {}
    assert step_names[:6] == [
        "input_resolver",
        "deterministic_pipeline",
        "fundamental_context_builder",
        "fundamental_analyst",
        "technical_context_builder",
        "technical_analyst",
    ]
    assert DECISION_RANK[response.decision] <= DECISION_RANK[baseline.decision]
    assert response.confidence <= baseline.confidence
    for field_name in RISK_FIELDS:
        assert getattr(response, field_name) is None or getattr(response, field_name) == getattr(baseline, field_name)
    non_downgrade_violations = [
        violation
        for violation in evaluate_agentic_invariants(
            deterministic_response=baseline,
            agentic_response=response,
            trace=response.trace,
            allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
        )
        if violation.code not in {"risk_field_mismatch", "synthesizer_changed_risk_number", "memo_risk_block_mismatch"}
    ]
    assert non_downgrade_violations == []


@pytest.mark.parametrize("embedding_client", [None, object()])
def test_workflow_degrades_gracefully_without_embedder_on_conflict(
    db_engine,
    monkeypatch,
    embedding_client,
):
    ticker = "NOEMB"
    _seed_full_security(db_engine, ticker=ticker, as_of_date=AS_OF_DATE)
    settings = _configure_modes(monkeypatch, critic_mode="off", synthesizer_contract="typed")
    monkeypatch.setattr(
        fundamental_context_module,
        "_build_embedding_client",
        lambda: MockEmbeddingClient(dimension=768),
    )
    baseline = _trade_baseline(ticker)
    monkeypatch.setattr(
        workflow_module,
        "resolve_ticker",
        lambda ref: SimpleNamespace(is_valid=True, ticker=ticker, error=None),
    )
    monkeypatch.setattr(workflow_module, "analyze_request", lambda req, engine=None: baseline.model_copy(deep=True))
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(assess=_positive_fundamental_assessment),
    )
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(assess=_conflicting_technical_assessment),
    )

    seen_tensions = []

    class _CheckingSynth:
        model_name = "checking-synth"

        def generate(self, prompt: str) -> ModelOutput:
            payload = json.loads(prompt.split("DecisionSynthesisRequest:\n", 1)[1])
            seen_tensions.append(payload["cross_domain_tension"])
            return ModelOutput(
                text=json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": payload["run_id"],
                        "context_hash": payload["context_hash"],
                        "proposed_decision": "watchlist",
                        "confidence": 0.60,
                        "thesis": "Tension noted without supplemental chunks.",
                        "key_reasons": [],
                        "key_risks": [],
                        "what_would_change": [],
                    }
                ),
                token_usage={},
            )

    workflow = AgenticAnalysisWorkflow(
        load_agent_config(settings),
        engine=db_engine,
        embedding_client=cast(Any, embedding_client),
        decision_synthesizer=DecisionSynthesizer(model_port=_CheckingSynth(), max_retries=0),
        runner_factory=lambda registry, sym: iter(_adk_events()),
    )

    response = workflow.analyze(ticker, request=AnalyzeRequest(ticker=ticker, as_of_date=AS_OF_DATE))

    assert len(seen_tensions) == 1
    assert seen_tensions[0] is not None
    assert seen_tensions[0]["supplemental_chunk_ids"] == []
    assert response.trace is not None
    assert "cross_domain_tension" in [step.agent_name for step in response.trace.steps]
    assert SPECIALIST_ORDER


def test_workflow_skips_tension_step_and_lookup_when_assessments_align(
    db_engine,
    monkeypatch,
):
    ticker = "ALIGN"
    _seed_full_security(db_engine, ticker=ticker, as_of_date=AS_OF_DATE)
    settings = _configure_modes(monkeypatch, critic_mode="off", synthesizer_contract="typed")
    baseline = _trade_baseline(ticker)
    monkeypatch.setattr(
        workflow_module,
        "resolve_ticker",
        lambda ref: SimpleNamespace(is_valid=True, ticker=ticker, error=None),
    )
    monkeypatch.setattr(workflow_module, "analyze_request", lambda req, engine=None: baseline.model_copy(deep=True))
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(assess=_positive_fundamental_assessment),
    )
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(assess=_aligned_technical_assessment),
    )

    embedder = DeterministicEmbeddingClient()
    workflow = AgenticAnalysisWorkflow(
        load_agent_config(settings),
        engine=db_engine,
        embedding_client=embedder,
        runner_factory=lambda registry, sym: iter(_adk_events()),
    )

    response = workflow.analyze(ticker, request=AnalyzeRequest(ticker=ticker, as_of_date=AS_OF_DATE))

    assert response.trace is not None
    assert "cross_domain_tension" not in [step.agent_name for step in response.trace.steps]
    # All embedder queries came only from fundamental_context (6 questions), zero tension lookup
    assert len(embedder.queries) == 6


def test_workflow_real_adk_default_runner_executes_cross_domain_tension_blackboard_node(
    db_engine,
    monkeypatch,
):
    ticker = "TNADK"
    _seed_full_security(db_engine, ticker=ticker, as_of_date=AS_OF_DATE)
    settings = _configure_modes(monkeypatch, critic_mode="off", synthesizer_contract="typed")
    monkeypatch.setattr(
        fundamental_context_module,
        "_build_embedding_client",
        lambda: MockEmbeddingClient(dimension=768),
    )
    baseline = _trade_baseline(ticker)
    monkeypatch.setattr(
        workflow_module,
        "resolve_ticker",
        lambda ref: SimpleNamespace(is_valid=True, ticker=ticker, error=None),
    )
    monkeypatch.setattr(workflow_module, "analyze_request", lambda req, engine=None: baseline.model_copy(deep=True))
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(assess=_positive_fundamental_assessment),
    )
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(assess=_conflicting_technical_assessment),
    )

    seen_tensions = []

    class _RealAdkCheckingSynth:
        model_name = "real-adk-synth"

        def generate(self, prompt: str) -> ModelOutput:
            payload = json.loads(prompt.split("DecisionSynthesisRequest:\n", 1)[1])
            seen_tensions.append(payload["cross_domain_tension"])
            return ModelOutput(
                text=json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": payload["run_id"],
                        "context_hash": payload["context_hash"],
                        "proposed_decision": "watchlist",
                        "confidence": 0.60,
                        "thesis": "Real ADK blackboard tension handled.",
                        "key_reasons": [],
                        "key_risks": [],
                        "what_would_change": [],
                    }
                ),
                token_usage={"total_token_count": 6},
            )

    workflow = AgenticAnalysisWorkflow(
        load_agent_config(settings),
        engine=db_engine,
        embedding_client=None,
        decision_synthesizer=DecisionSynthesizer(model_port=_RealAdkCheckingSynth(), max_retries=0),
    )

    response = workflow.analyze(ticker, request=AnalyzeRequest(ticker=ticker, as_of_date=AS_OF_DATE))

    assert len(seen_tensions) == 1
    assert seen_tensions[0] is not None
    assert seen_tensions[0]["conflict_type"] == "polarity_conflict"
    assert response.trace is not None
    step_names = [step.agent_name for step in response.trace.steps]
    assert "cross_domain_tension" in step_names
    tension_step = next(step for step in response.trace.steps if step.agent_name == "cross_domain_tension")
    assert tension_step.token_usage == {}
    assert evaluate_agentic_invariants(
        deterministic_response=baseline,
        agentic_response=response,
        trace=response.trace,
        allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
    ) == [
        v
        for v in evaluate_agentic_invariants(
            deterministic_response=baseline,
            agentic_response=response,
            trace=response.trace,
            allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
        )
        if v.code in {"risk_field_mismatch", "synthesizer_changed_risk_number", "memo_risk_block_mismatch"}
    ]
