from __future__ import annotations

from datetime import date, datetime, timezone

from app.schemas.analyze import AnalyzeResponse, DecisionDiagnosis
from app.schemas.critique import DraftRecommendation
from app.schemas.fundamentals import CitedFinding, EvidenceChunkInput
from app.services.critique_context import (
    MAX_EVIDENCE_CHARACTERS,
    MAX_EVIDENCE_CHUNKS,
    build_analyst_views,
    build_critique_request,
    build_draft_from_response,
)
from app.services.fundamental_context import build_fundamental_analysis_request
from app.schemas.fundamentals import (
    FundamentalAssessmentResponse,
    FundamentalAssessmentResult,
    FundamentalAssessmentValidation,
)
from tests.test_fundamental_context import _fake_retriever, _seed_security_with_fundamentals

SNAPSHOT_AT = datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc)


def test_context_hash_is_stable_across_run_ids(db_engine, monkeypatch):
    _seed_security_with_fundamentals(db_engine)
    _patch_context_builder(
        db_engine,
        monkeypatch,
        evidence_chunks=[_evidence_chunk(10, "Primary evidence")],
    )

    first = build_critique_request(
        "ctx1",
        "run-a",
        draft=_watchlist_draft(),
        analyst_views=build_analyst_views(),
        deterministic_warnings=["warning-a"],
        deterministic_diagnosis=_diagnosis(),
        as_of=date(2026, 9, 8),
        engine=db_engine,
    )
    second = build_critique_request(
        "CTX1",
        "run-b",
        draft=_watchlist_draft(),
        analyst_views=build_analyst_views(),
        deterministic_warnings=["warning-a"],
        deterministic_diagnosis=_diagnosis(),
        as_of=date(2026, 9, 8),
        engine=db_engine,
    )

    assert first.context_hash == second.context_hash


def test_context_hash_changes_when_included_fields_change(db_engine, monkeypatch):
    _seed_security_with_fundamentals(db_engine)
    base_chunk = _evidence_chunk(10, "Base evidence")
    changed_chunk = _evidence_chunk(10, "Changed evidence")

    base_request = _build_request(
        db_engine,
        monkeypatch,
        evidence_chunks=[base_chunk],
        draft=_watchlist_draft(),
        warnings=["warning-a"],
    )
    changed_evidence_request = _build_request(
        db_engine,
        monkeypatch,
        evidence_chunks=[changed_chunk],
        draft=_watchlist_draft(),
        warnings=["warning-a"],
    )
    changed_draft_request = _build_request(
        db_engine,
        monkeypatch,
        evidence_chunks=[base_chunk],
        draft=_trade_draft(),
        warnings=["warning-a"],
    )
    changed_warning_request = _build_request(
        db_engine,
        monkeypatch,
        evidence_chunks=[base_chunk],
        draft=_watchlist_draft(),
        warnings=["warning-b"],
    )

    assert base_request.context_hash != changed_evidence_request.context_hash
    assert base_request.context_hash != changed_draft_request.context_hash
    assert base_request.context_hash != changed_warning_request.context_hash


def test_build_draft_from_response_copies_money_fields_verbatim():
    response = _trade_response(
        entry_range=(123.45, 125.67),
        stop_loss=118.91,
        take_profit=(131.11, 137.77),
        risk_reward=2.3456,
        position_size_eur=987.65,
    )

    draft = build_draft_from_response(response)

    assert draft.entry_range == response.entry_range
    assert draft.stop_loss == response.stop_loss
    assert draft.take_profit == response.take_profit
    assert draft.risk_reward == response.risk_reward
    assert draft.position_size_eur == response.position_size_eur


def test_trade_response_builds_valid_draft_recommendation():
    draft = build_draft_from_response(_trade_response())

    assert isinstance(draft, DraftRecommendation)
    assert draft.decision == "trade"


def test_build_analyst_views_includes_absent_technical_view():
    views = build_analyst_views(fundamental=_fundamental_result(), technical=None)

    assert [view.agent_name for view in views] == ["fundamental_analyst", "technical_analyst"]
    assert views[0].status == "completed"
    assert views[0].summary == "Fundamentals are supportive."
    assert views[1].status == "absent"
    assert views[1].overall_signal == "neutral"


def test_build_analyst_views_returns_two_absent_views():
    views = build_analyst_views()

    assert [view.agent_name for view in views] == ["fundamental_analyst", "technical_analyst"]
    assert all(view.status == "absent" for view in views)


def test_chunk_cap_is_deterministic(db_engine, monkeypatch):
    _seed_security_with_fundamentals(db_engine)
    evidence_chunks = [
        _evidence_chunk(chunk_id, f"Evidence {chunk_id}", retrieval_question_id=f"question-{100 - chunk_id}")
        for chunk_id in range(100, 0, -1)
    ]

    first = _build_request(
        db_engine,
        monkeypatch,
        evidence_chunks=evidence_chunks,
        draft=_watchlist_draft(),
        warnings=["warning-a"],
    )
    second = _build_request(
        db_engine,
        monkeypatch,
        evidence_chunks=evidence_chunks,
        draft=_watchlist_draft(),
        warnings=["warning-a"],
    )

    assert len(first.evidence_chunks) == MAX_EVIDENCE_CHUNKS
    assert [chunk.chunk_id for chunk in first.evidence_chunks] == [
        chunk.chunk_id for chunk in second.evidence_chunks
    ]


def test_character_cap_drops_whole_chunks_without_truncating_text(db_engine, monkeypatch):
    _seed_security_with_fundamentals(db_engine)
    evidence_chunks = [
        _evidence_chunk(1, "A" * 30_000),
        _evidence_chunk(2, "B" * 30_000),
        _evidence_chunk(3, "C" * 10),
    ]

    request = _build_request(
        db_engine,
        monkeypatch,
        evidence_chunks=evidence_chunks,
        draft=_watchlist_draft(),
        warnings=["warning-a"],
    )

    assert [chunk.chunk_id for chunk in request.evidence_chunks] == [1, 2]
    assert sum(len(chunk.text) for chunk in request.evidence_chunks) == MAX_EVIDENCE_CHARACTERS
    assert request.evidence_chunks[0].text == "A" * 30_000
    assert request.evidence_chunks[1].text == "B" * 30_000


def test_duplicate_warnings_are_collapsed_in_order(db_engine, monkeypatch):
    _seed_security_with_fundamentals(db_engine)

    request = _build_request(
        db_engine,
        monkeypatch,
        evidence_chunks=[_evidence_chunk(10, "Evidence")],
        draft=_watchlist_draft(),
        warnings=["warning-a", "warning-b", "warning-a", "warning-c", "warning-b"],
    )

    assert request.deterministic_warnings == ["warning-a", "warning-b", "warning-c"]


def _build_request(
    db_engine,
    monkeypatch,
    *,
    evidence_chunks: list[EvidenceChunkInput],
    draft: DraftRecommendation,
    warnings: list[str],
):
    _patch_context_builder(db_engine, monkeypatch, evidence_chunks=evidence_chunks)
    return build_critique_request(
        "CTX1",
        "run-test",
        draft=draft,
        analyst_views=build_analyst_views(),
        deterministic_warnings=warnings,
        deterministic_diagnosis=_diagnosis(),
        as_of=date(2026, 9, 8),
        engine=db_engine,
    )


def _patch_context_builder(db_engine, monkeypatch, *, evidence_chunks: list[EvidenceChunkInput]) -> None:
    def fake_builder(ticker: str, run_id: str, as_of=None, *, engine=None):
        del run_id
        base_request = build_fundamental_analysis_request(
            ticker,
            "fundamental-run",
            as_of=as_of,
            engine=engine or db_engine,
            retriever=_fake_retriever([]),
            snapshot_at=SNAPSHOT_AT,
        )
        return base_request.model_copy(update={"evidence_chunks": evidence_chunks})

    monkeypatch.setattr("app.services.critique_context.build_fundamental_analysis_request", fake_builder)


def _watchlist_draft() -> DraftRecommendation:
    return build_draft_from_response(
        AnalyzeResponse(
            ticker="CTX1",
            decision="watchlist",
            confidence=0.35,
            reasons=["Needs confirmation"],
            warnings=["earnings soon"],
            engine_version="test-engine",
            trace_id="trace-1",
            diagnosis=_diagnosis(),
        )
    )


def _trade_draft() -> DraftRecommendation:
    return build_draft_from_response(_trade_response())


def _trade_response(
    *,
    entry_range=(100.12, 101.34),
    stop_loss=95.67,
    take_profit=(108.9, 112.34),
    risk_reward=2.718,
    position_size_eur=1500.55,
) -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="CTX1",
        decision="trade",
        confidence=0.82,
        reasons=["Momentum and fundamentals align"],
        warnings=["stale_price_data"],
        entry_range=entry_range,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        position_size_eur=position_size_eur,
        engine_version="test-engine",
        trace_id="trace-2",
        diagnosis=_diagnosis(),
    )


def _diagnosis() -> DecisionDiagnosis:
    return DecisionDiagnosis(
        stage="checklist",
        rule_id="score-threshold",
        detail="Checklist supports a watchlist decision.",
        checklist_score=7,
        checklist_max=11,
        missing_data=[],
        debug_reason="deterministic_test",
    )


def _fundamental_result() -> FundamentalAssessmentResult:
    finding = CitedFinding(
        finding_id="finding-1",
        category="growth_quality",
        direction="positive",
        materiality="medium",
        claim="Revenue growth accelerated.",
        metric_ids=["revenue_growth:2026-06-30"],
        chunk_ids=[10],
    )
    response = FundamentalAssessmentResponse(
        run_id="run-1",
        context_hash="context-1",
        status="completed",
        overall_signal="positive",
        proposed_score_adjustment=1,
        findings=[finding],
        contradictions=[],
        material_red_flags=[],
        evidence_coverage=1.0,
        missing_information=[],
        summary="Fundamentals are supportive.",
    )
    return FundamentalAssessmentResult(
        response=response,
        validation=FundamentalAssessmentValidation(accepted=True, reason_codes=[]),
        model_name="test-model",
        prompt_version="1.0",
    )


def _evidence_chunk(
    chunk_id: int,
    text: str,
    *,
    retrieval_question_id: str = "question-1",
) -> EvidenceChunkInput:
    return EvidenceChunkInput(
        chunk_id=chunk_id,
        retrieval_question_id=retrieval_question_id,
        source_type="filing_mda",
        source_url=f"https://example.com/{chunk_id}",
        source_hash=f"hash-{chunk_id}",
        chunk_index=0,
        published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        quality_tier="primary",
        text=text,
    )
