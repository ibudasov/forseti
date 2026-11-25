from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any, cast

from pydantic import ValidationError
import pytest

from app.db.models import DocumentChunk, SourceQualityTier, SourceType
from app.db.repository import similarity_search, upsert_document_chunks
from app.domain.technicals import DeterministicTechnicalAnalysis
from app.rag.ingestion.base import compute_source_hash
from app.rag.retrieval import retrieve
from app.schemas.analyze import AnalyzeResponse, DecisionDiagnosis
from app.schemas.critique import CritiqueObjection, CritiqueResponse
from app.schemas.fundamentals import (
    CitedFinding,
    DeterministicFundamentalAnalysis,
    EvidenceChunkInput,
    FundamentalAnalysisRequest,
    FundamentalAssessmentResponse,
    FundamentalAssessmentResult,
    FundamentalAssessmentValidation,
    FundamentalContextCoverage,
)
from app.schemas.technicals import (
    TechnicalAnalysisRequest,
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
    TechnicalAssessmentValidation,
    TechnicalContextCoverage,
    TechnicalFact,
    TechnicalFinding,
)
from app.schemas.tension import CrossDomainTension
import app.services.cross_domain_tension as tension_module
from app.services.critic import _to_gemini_schema, build_critic_prompt, validate_critique
from app.services.critique_context import (
    MAX_EVIDENCE_CHARACTERS,
    MAX_EVIDENCE_CHUNKS,
    build_analyst_views,
    build_critique_request,
    build_draft_from_response,
)
from app.services.decision_synthesizer import (
    DecisionSynthesizer,
    ModelOutput,
    validate_decision_memo,
)
from app.services.llm_schema import inline_response_schema
from app.services.synthesis_context import build_synthesis_request

SNAPSHOT_AT = datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc)
AS_OF_DATE = date(2026, 9, 8)


class _UnitVectorEmbeddingClient:
    def __init__(self, vector: list[float] | None = None) -> None:
        self._vector = vector or ([1.0] + [0.0] * 767)
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self._vector) for _ in texts]


def test_cross_domain_tension_schema_is_frozen_strict_and_gemini_compatible():
    tension = CrossDomainTension(
        conflict_type="polarity_conflict",
        fundamental_signal="positive",
        technical_signal="negative",
        technical_trend_state="downtrend",
        technical_momentum_state="weakening",
        fundamental_metric_ids=["revenue_growth:Q2"],
        fundamental_chunk_ids=[10],
        technical_metric_ids=["rsi_14:2026-09-08"],
        technical_risk_flag_categories=[],
        retrieval_query="NVDA polarity conflict",
        date_window_days=90,
        supplemental_chunk_ids=[20],
        summary="Fundamental positive conflicts with technical negative.",
    )
    with pytest.raises(ValidationError):
        CrossDomainTension.model_validate({**tension.model_dump(), "unexpected": 1})
    with pytest.raises(ValidationError):
        setattr(tension, "summary", "mutated")

    inlined = inline_response_schema(CrossDomainTension)
    assert "$defs" not in inlined
    gemini_schema = _to_gemini_schema(CrossDomainTension.model_json_schema())
    assert gemini_schema["type"] == "OBJECT"


def test_cross_domain_tension_schema_rejects_mutating_or_empty_fields():
    base = {
        "conflict_type": "polarity_conflict",
        "fundamental_signal": "positive",
        "technical_signal": "negative",
        "technical_trend_state": "downtrend",
        "technical_momentum_state": "weakening",
        "retrieval_query": "NVDA conflict",
        "date_window_days": 90,
        "summary": "Summary.",
    }
    with pytest.raises(ValidationError):
        CrossDomainTension.model_validate({**base, "extra_field": "x"})
    with pytest.raises(ValidationError):
        CrossDomainTension.model_validate({**base, "date_window_days": 0})
    with pytest.raises(ValidationError):
        CrossDomainTension.model_validate({**base, "summary": "   "})
    with pytest.raises(ValidationError):
        CrossDomainTension.model_validate({**base, "supplemental_chunk_ids": [1, 1]})


@pytest.mark.parametrize(
    ("fund_signal", "tech_signal", "high_risk_flag", "expected_conflict"),
    [
        ("positive", "strong_negative", False, "signal_conflict"),
        ("strong_positive", "strong_negative", False, "signal_conflict"),
        ("strong_negative", "positive", False, "signal_conflict"),
        ("positive", "negative", False, "polarity_conflict"),
        ("negative", "positive", False, "polarity_conflict"),
        ("positive", "positive", True, "technical_risk_flag"),
        ("strong_positive", "neutral", True, "technical_risk_flag"),
        ("positive", "positive", False, None),
        ("negative", "negative", True, None),
        ("neutral", "negative", True, None),
        ("neutral", "positive", True, None),
    ],
)
def test_detect_cross_domain_tension_matrix(
    fund_signal,
    tech_signal,
    high_risk_flag,
    expected_conflict,
):
    fund_result = _fundamental_result(signal=fund_signal)
    tech_result = _technical_result(signal=tech_signal, high_risk_flag=high_risk_flag)
    tech_req = _technical_request(days_to_earnings=10)

    tension = tension_module.detect_cross_domain_tension(
        ticker="NVDA",
        fundamental_assessment=fund_result,
        technical_assessment=tech_result,
        technical_request=tech_req,
    )

    if expected_conflict is None:
        assert tension is None
        return
    assert tension is not None
    assert tension.conflict_type == expected_conflict
    assert tension.fundamental_signal == fund_signal
    assert tension.technical_signal == tech_signal
    assert tension.date_window_days == 70
    if high_risk_flag:
        assert tension.technical_risk_flag_categories == ["event_risk"]


@pytest.mark.parametrize(
    ("fund_kwargs", "tech_kwargs"),
    [
        ({"absent": True}, {}),
        ({}, {"absent": True}),
        ({"status": "insufficient_data"}, {}),
        ({}, {"status": "failed"}),
        ({"accepted": False}, {}),
        ({}, {"accepted": False}),
    ],
)
def test_detect_cross_domain_tension_returns_none_for_incomplete_or_rejected(
    fund_kwargs,
    tech_kwargs,
):
    fund_result = None if fund_kwargs.get("absent") else _fundamental_result(**fund_kwargs)
    tech_result = None if tech_kwargs.get("absent") else _technical_result(**tech_kwargs)

    resolution = tension_module.resolve_cross_domain_tension(
        ticker="NVDA",
        snapshot_at=SNAPSHOT_AT,
        fundamental_assessment=fund_result,
        technical_assessment=tech_result,
        embedding_client=_UnitVectorEmbeddingClient(),
    )

    assert resolution.tension is None
    assert resolution.supplemental_chunks == ()


def test_resolve_cross_domain_tension_bounds_lookup_deduplicates_and_filters_pit(monkeypatch):
    calls: list[dict] = []
    existing_chunk = _evidence_chunk(10, "Already seen fundamental chunk.")
    fundamental_req = _fundamental_request(evidence_chunks=[existing_chunk])
    candidates = [
        _doc_chunk(10, "Duplicate fundamental chunk.", published_at=SNAPSHOT_AT - timedelta(days=2)),
        _doc_chunk(99, "Future leak chunk.", published_at=SNAPSHOT_AT + timedelta(seconds=1)),
        _doc_chunk(20, "Supplemental catalyst 1.", published_at=SNAPSHOT_AT - timedelta(days=1)),
        _doc_chunk(20, "Duplicate candidate 20.", published_at=SNAPSHOT_AT - timedelta(days=1)),
        _doc_chunk(21, "Supplemental catalyst 2.", published_at=SNAPSHOT_AT - timedelta(days=3)),
        _doc_chunk(22, "Supplemental catalyst 3.", published_at=SNAPSHOT_AT - timedelta(days=4)),
        _doc_chunk(23, "Supplemental catalyst 4.", published_at=SNAPSHOT_AT - timedelta(days=5)),
        _doc_chunk(24, "Exceeds chunk cap 5.", published_at=SNAPSHOT_AT - timedelta(days=6)),
    ]

    def fake_retrieve(**kwargs):
        calls.append(kwargs)
        return list(candidates)

    monkeypatch.setattr(tension_module, "retrieve", fake_retrieve)

    resolution = tension_module.resolve_cross_domain_tension(
        ticker="NVDA",
        snapshot_at=SNAPSHOT_AT,
        fundamental_assessment=_fundamental_result(signal="positive"),
        technical_assessment=_technical_result(signal="negative"),
        fundamental_request=fundamental_req,
        technical_request=_technical_request(),
        embedding_client=_UnitVectorEmbeddingClient(),
    )

    assert len(calls) == 1
    assert calls[0]["published_before"] == SNAPSHOT_AT
    assert resolution.tension is not None
    assert resolution.tension.supplemental_chunk_ids == [20, 21, 22, 23]
    assert [chunk.chunk_id for chunk in resolution.supplemental_chunks] == [20, 21, 22, 23]
    assert 99 not in resolution.tension.supplemental_chunk_ids
    assert 10 not in resolution.tension.supplemental_chunk_ids


@pytest.mark.parametrize("embedding_client", [None, object()])
def test_resolve_cross_domain_tension_degrades_gracefully_without_embedder(
    embedding_client,
    monkeypatch,
):
    def fail_retrieve(**_kwargs):
        raise AssertionError("retrieve must not be called when embedder is absent")

    monkeypatch.setattr(tension_module, "retrieve", fail_retrieve)

    resolution = tension_module.resolve_cross_domain_tension(
        ticker="NVDA",
        snapshot_at=SNAPSHOT_AT,
        fundamental_assessment=_fundamental_result(signal="positive"),
        technical_assessment=_technical_result(signal="strong_negative"),
        embedding_client=cast(Any, embedding_client),
    )

    assert resolution.tension is not None
    assert resolution.tension.conflict_type == "signal_conflict"
    assert resolution.tension.supplemental_chunk_ids == []
    assert resolution.supplemental_chunks == ()


def test_resolve_cross_domain_tension_swallows_retrieval_exception(monkeypatch):
    def raising_retrieve(**_kwargs):
        raise RuntimeError("pgvector timeout")

    monkeypatch.setattr(tension_module, "retrieve", raising_retrieve)

    resolution = tension_module.resolve_cross_domain_tension(
        ticker="NVDA",
        snapshot_at=SNAPSHOT_AT,
        fundamental_assessment=_fundamental_result(signal="positive"),
        technical_assessment=_technical_result(signal="negative"),
        embedding_client=_UnitVectorEmbeddingClient(),
    )

    assert resolution.tension is not None
    assert resolution.tension.supplemental_chunk_ids == []
    assert resolution.supplemental_chunks == ()


def test_pgvector_similarity_search_and_retrieve_enforce_published_before_including_sector_fallback(
    pgvector_engine,
):
    emb = [1.0] + [0.0] * 767
    snapshot = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    past_chunk = _doc_chunk(
        1,
        "Historical 2024 primary filing.",
        published_at=snapshot - timedelta(days=10),
        ticker="PITX",
        source_type=SourceType.filing_business,
        embedding=emb,
    )
    future_chunk = _doc_chunk(
        2,
        "Future 2024 company news that must be filtered.",
        published_at=snapshot + timedelta(days=1),
        ticker="PITX",
        source_type=SourceType.company_news,
        embedding=emb,
    )
    past_sector = _doc_chunk(
        3,
        "Historical 2024 sector news.",
        published_at=snapshot - timedelta(days=5),
        ticker="PITX",
        source_type=SourceType.sector_news,
        quality_tier=SourceQualityTier.secondary_reputable,
        embedding=emb,
    )
    future_sector = _doc_chunk(
        4,
        "Future 2024 sector news that must be filtered.",
        published_at=snapshot + timedelta(days=2),
        ticker="PITX",
        source_type=SourceType.sector_news,
        quality_tier=SourceQualityTier.secondary_reputable,
        embedding=emb,
    )
    upsert_document_chunks([past_chunk, future_chunk, past_sector, future_sector], engine=pgvector_engine)

    direct = similarity_search(
        ticker="PITX",
        query_embedding=emb,
        top_k=10,
        published_after=snapshot - timedelta(days=90),
        published_before=snapshot,
        engine=pgvector_engine,
    )
    assert sorted(chunk.text for chunk in direct) == [
        "Historical 2024 primary filing.",
        "Historical 2024 sector news.",
    ]

    # Force sector fallback (primary returns only 1 chunk < top_k // 2 = 3)
    retrieved = retrieve(
        ticker="PITX",
        question="historical sector risk",
        embedding_client=_UnitVectorEmbeddingClient(emb),
        top_k=6,
        source_types=[SourceType.filing_business],
        date_window_days=90,
        published_before=snapshot,
        engine=pgvector_engine,
    )
    texts = {chunk.text for chunk in retrieved}
    assert "Historical 2024 primary filing." in texts
    assert "Historical 2024 sector news." in texts
    assert "Future 2024 company news that must be filtered." not in texts
    assert "Future 2024 sector news that must be filtered." not in texts


def test_synthesis_request_preserves_hash_when_empty_and_grounds_supplemental_chunk_numbers():
    response = _trade_response()
    views = build_analyst_views(
        fundamental=_fundamental_result(signal="positive"),
        technical=_technical_result(signal="negative"),
    )
    baseline_req = build_synthesis_request(
        run_id="run-1",
        response=response,
        analyst_views=views,
        as_of_date=AS_OF_DATE,
        snapshot_at=SNAPSHOT_AT,
    )
    explicit_none_req = build_synthesis_request(
        run_id="run-2",
        response=response,
        analyst_views=views,
        as_of_date=AS_OF_DATE,
        snapshot_at=SNAPSHOT_AT,
        cross_domain_tension=None,
        supplemental_evidence_chunks=[],
    )
    assert baseline_req.context_hash == explicit_none_req.context_hash

    supp_chunk = _evidence_chunk(77, "Customer concentration reached 42% in Q2.")
    tension = _sample_tension(supplemental_chunk_ids=[77])
    tension_req = build_synthesis_request(
        run_id="run-3",
        response=response,
        analyst_views=views,
        as_of_date=AS_OF_DATE,
        snapshot_at=SNAPSHOT_AT,
        cross_domain_tension=tension,
        supplemental_evidence_chunks=[supp_chunk],
    )
    assert tension_req.context_hash != baseline_req.context_hash
    assert 77 in tension_req.known_chunk_ids

    valid_memo = json.dumps(
        {
            "schema_version": "1.0",
            "run_id": tension_req.run_id,
            "context_hash": tension_req.context_hash,
            "proposed_decision": "watchlist",
            "confidence": 0.55,
            "thesis": "Cross-domain tension warrants caution.",
            "key_reasons": [
                {
                    "claim": "Customer concentration is 42% despite supportive headline growth.",
                    "metric_ids": [],
                    "chunk_ids": [77],
                    "warning_codes": [],
                }
            ],
            "key_risks": [],
            "what_would_change": [],
        }
    )

    class _Model:
        model_name = "test-synth"

        def generate(self, prompt: str) -> ModelOutput:
            assert "cross_domain_tension" in prompt
            return ModelOutput(text=valid_memo, token_usage={"total_token_count": 10})

    result = DecisionSynthesizer(model_port=_Model(), max_retries=0).synthesize(tension_req)
    assert result.validation.accepted is True
    assert result.used_fallback is False

    bad_memo = result.response.model_copy(
        update={
            "key_reasons": [
                result.response.key_reasons[0].model_copy(
                    update={"claim": "Customer concentration is 99% despite growth."}
                )
            ]
        }
    )
    bad_validation = validate_decision_memo(bad_memo, tension_req)
    assert bad_validation.accepted is False
    assert "unsupported_numeric_claim" in bad_validation.reason_codes


def test_build_critique_request_reuses_fundamental_request_deduplicates_and_preserves_hash(monkeypatch):
    def forbid_rebuild(*_args, **_kwargs):
        raise AssertionError("build_fundamental_analysis_request must not be called when fundamental_request is supplied")

    monkeypatch.setattr("app.services.critique_context.build_fundamental_analysis_request", forbid_rebuild)

    base_chunks = [_evidence_chunk(10, "Base chunk 10."), _evidence_chunk(11, "Base chunk 11.")]
    fundamental_req = _fundamental_request(evidence_chunks=base_chunks)
    draft = build_draft_from_response(_trade_response())
    views = build_analyst_views(
        fundamental=_fundamental_result(signal="positive"),
        technical=_technical_result(signal="negative"),
    )

    without_tension = build_critique_request(
        "NVDA",
        "run-a",
        draft=draft,
        analyst_views=views,
        deterministic_warnings=[],
        fundamental_request=fundamental_req,
    )
    direct_default = build_decision_synthesis_hash_parity_critique(
        fundamental_req=fundamental_req,
        draft=draft,
        views=views,
    )
    assert without_tension.context_hash == direct_default.context_hash
    assert "Cross-domain tension:" not in build_critic_prompt(without_tension)

    tension = _sample_tension(supplemental_chunk_ids=[11, 77])
    with_tension = build_critique_request(
        "NVDA",
        "run-b",
        draft=draft,
        analyst_views=views,
        deterministic_warnings=[],
        fundamental_request=fundamental_req,
        cross_domain_tension=tension,
        supplemental_evidence_chunks=[
            _evidence_chunk(11, "Duplicate 11 should be dropped."),
            _evidence_chunk(77, "Supplemental chunk 77."),
        ],
    )
    assert with_tension.context_hash != without_tension.context_hash
    assert [chunk.chunk_id for chunk in with_tension.evidence_chunks] == [10, 11, 77]
    assert "Cross-domain tension:" in build_critic_prompt(with_tension)

    critique_response = CritiqueResponse(
        run_id=with_tension.run_id,
        context_hash=with_tension.context_hash,
        status="completed",
        verdict="revise",
        objections=[
            CritiqueObjection(
                objection_id="model:tension:1",
                category="analyst_contradiction",
                severity="medium",
                source="model",
                claim="Supplemental evidence highlights concentration tension.",
                chunk_ids=[77],
            )
        ],
        revision_instructions="Address supplemental chunk 77.",
        summary="Address tension.",
    )
    validation = validate_critique(critique_response, with_tension)
    assert validation.accepted is True


def test_build_critique_request_caps_merged_chunks_at_24_and_60000_chars(monkeypatch):
    monkeypatch.setattr(
        "app.services.critique_context.build_fundamental_analysis_request",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected rebuild")),
    )
    base_chunks = [_evidence_chunk(idx, "A" * 2_000) for idx in range(1, 21)]
    supp_chunks = [_evidence_chunk(idx, "B" * 2_000) for idx in range(21, 31)]
    fundamental_req = _fundamental_request(evidence_chunks=base_chunks)

    chunk_capped = build_critique_request(
        "NVDA",
        "run-cap",
        draft=build_draft_from_response(_trade_response()),
        analyst_views=build_analyst_views(),
        deterministic_warnings=[],
        fundamental_request=fundamental_req,
        supplemental_evidence_chunks=supp_chunks,
    )

    assert len(chunk_capped.evidence_chunks) == MAX_EVIDENCE_CHUNKS
    assert sum(len(chunk.text) for chunk in chunk_capped.evidence_chunks) == 48_000

    large_base = [_evidence_chunk(idx, "C" * 3_000) for idx in range(1, 16)]
    large_supp = [_evidence_chunk(idx, "D" * 3_000) for idx in range(16, 25)]
    char_capped = build_critique_request(
        "NVDA",
        "run-char-cap",
        draft=build_draft_from_response(_trade_response()),
        analyst_views=build_analyst_views(),
        deterministic_warnings=[],
        fundamental_request=_fundamental_request(evidence_chunks=large_base),
        supplemental_evidence_chunks=large_supp,
    )

    assert len(char_capped.evidence_chunks) == 20
    assert sum(len(chunk.text) for chunk in char_capped.evidence_chunks) == MAX_EVIDENCE_CHARACTERS


def build_decision_synthesis_hash_parity_critique(*, fundamental_req, draft, views):
    return build_critique_request(
        "NVDA",
        "run-parity",
        draft=draft,
        analyst_views=views,
        deterministic_warnings=[],
        fundamental_request=fundamental_req,
        cross_domain_tension=None,
        supplemental_evidence_chunks=(),
    )


def _sample_tension(*, supplemental_chunk_ids: list[int]) -> CrossDomainTension:
    return CrossDomainTension(
        conflict_type="polarity_conflict",
        fundamental_signal="positive",
        technical_signal="negative",
        technical_trend_state="downtrend",
        technical_momentum_state="weakening",
        fundamental_metric_ids=["revenue_growth:Q2"],
        fundamental_chunk_ids=[10],
        technical_metric_ids=["rsi_14:2026-09-08"],
        technical_risk_flag_categories=[],
        retrieval_query="NVDA conflict",
        date_window_days=90,
        supplemental_chunk_ids=supplemental_chunk_ids,
        summary="Conflict between fundamental and technical signals.",
    )


def _trade_response() -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision="trade",
        confidence=0.75,
        reasons=["Checklist passed"],
        warnings=[],
        entry_range=(100.0, 102.0),
        stop_loss=95.0,
        take_profit=(110.0, 115.0),
        risk_reward=2.5,
        position_size_eur=1000.0,
        engine_version="test",
        trace_id="run-1",
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="trade",
            detail="Trade",
            checklist_score=9,
            checklist_max=11,
            missing_data=[],
            debug_reason="ok",
        ),
    )


def _fundamental_request(*, evidence_chunks: list[EvidenceChunkInput]) -> FundamentalAnalysisRequest:
    return FundamentalAnalysisRequest(
        run_id="fund-run",
        context_hash="fund-hash",
        ticker="NVDA",
        company_name="NVIDIA",
        sector="semiconductors",
        currency="USD",
        snapshot_at=SNAPSHOT_AT,
        as_of_date=AS_OF_DATE,
        deterministic_result=DeterministicFundamentalAnalysis(
            ticker="NVDA",
            as_of_date=AS_OF_DATE,
            score=0,
            maximum_score=6,
            warnings=[],
        ),
        evidence_chunks=evidence_chunks,
        coverage=FundamentalContextCoverage(),
    )


def _technical_request(*, days_to_earnings: int | None = None) -> TechnicalAnalysisRequest:
    facts = []
    if days_to_earnings is not None:
        facts.append(
            TechnicalFact(
                metric_id=f"days_to_earnings:{AS_OF_DATE.isoformat()}",
                name="days_to_earnings",
                value=Decimal(days_to_earnings),
                unit="days",
                derivation="calendar days to earnings",
            )
        )
    return TechnicalAnalysisRequest(
        run_id="tech-run",
        context_hash="tech-hash",
        ticker="NVDA",
        sector="semiconductors",
        snapshot_at=SNAPSHOT_AT,
        as_of_date=AS_OF_DATE,
        deterministic_result=DeterministicTechnicalAnalysis(
            ticker="NVDA",
            as_of_date=AS_OF_DATE,
            score=0,
            rule_results=[],
            metrics=[],
        ),
        facts=facts,
        coverage=TechnicalContextCoverage(bar_count=60),
    )


def _fundamental_result(
    *,
    signal: Any = "positive",
    status: Any = "completed",
    accepted: bool = True,
) -> FundamentalAssessmentResult:
    return FundamentalAssessmentResult(
        response=FundamentalAssessmentResponse(
            run_id="fund-run",
            context_hash="fund-hash",
            status=status,
            overall_signal=signal,
            proposed_score_adjustment=0,
            findings=[
                CitedFinding(
                    finding_id="f1",
                    category="growth_quality",
                    direction="positive",
                    materiality="medium",
                    claim="Revenue grew 20%.",
                    metric_ids=["revenue_growth:Q2"],
                    chunk_ids=[10],
                )
            ]
            if status == "completed"
            else [],
            evidence_coverage=1.0,
            summary="Fundamental view.",
        ),
        validation=FundamentalAssessmentValidation(accepted=accepted, reason_codes=[] if accepted else ["invalid"]),
        model_name="test-fund",
        prompt_version="1.0",
    )


def _technical_result(
    *,
    signal: Any = "negative",
    status: Any = "completed",
    accepted: bool = True,
    high_risk_flag: bool = False,
) -> TechnicalAssessmentResult:
    risk_flags = (
        [
            TechnicalFinding(
                finding_id="rf1",
                category="event_risk",
                direction="negative",
                materiality="high",
                claim="Earnings in 10 days.",
                metric_ids=["days_to_earnings:2026-09-08"],
            )
        ]
        if high_risk_flag and status == "completed"
        else []
    )
    return TechnicalAssessmentResult(
        response=TechnicalAssessmentResponse(
            run_id="tech-run",
            context_hash="tech-hash",
            status=status,
            overall_signal=signal,
            trend_state="downtrend" if signal in {"negative", "strong_negative"} else "uptrend",
            momentum_state="weakening" if signal in {"negative", "strong_negative"} else "strengthening",
            proposed_score_adjustment=0,
            findings=[
                TechnicalFinding(
                    finding_id="t1",
                    category="momentum",
                    direction="negative" if signal in {"negative", "strong_negative"} else "positive",
                    materiality="medium",
                    claim="RSI is 42.",
                    metric_ids=["rsi_14:2026-09-08"],
                )
            ]
            if status == "completed"
            else [],
            risk_flags=risk_flags,
            summary="Technical view.",
        ),
        validation=TechnicalAssessmentValidation(accepted=accepted, reason_codes=[] if accepted else ["invalid"]),
        model_name="test-tech",
        prompt_version="1.0",
    )


def _evidence_chunk(chunk_id: int, text: str) -> EvidenceChunkInput:
    return EvidenceChunkInput(
        chunk_id=chunk_id,
        retrieval_question_id="growth_sustainability",
        source_type="filing_mda",
        source_url=f"https://example.com/{chunk_id}",
        source_hash=f"hash-{chunk_id}",
        chunk_index=0,
        published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        quality_tier="primary",
        text=text,
    )


def _doc_chunk(
    chunk_id: int,
    text: str,
    *,
    published_at: datetime | None,
    ticker: str = "NVDA",
    source_type: SourceType = SourceType.filing_mda,
    quality_tier: SourceQualityTier = SourceQualityTier.primary_regulatory,
    embedding: list[float] | None = None,
) -> DocumentChunk:
    url = f"https://example.com/{ticker}/{chunk_id}"
    return DocumentChunk(
        id=chunk_id,
        ticker=ticker,
        source_type=source_type,
        document_id=f"doc:{ticker}:{chunk_id}",
        source_url=url,
        publisher="Test",
        title=f"Chunk {chunk_id}",
        source_quality_tier=quality_tier,
        source_hash=compute_source_hash(url, chunk_id, text),
        published_at=published_at,
        ingested_at=SNAPSHOT_AT,
        chunk_index=chunk_id,
        text=text,
        embedding=embedding,
    )
