from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.fundamentals import DeterministicFundamentalAnalysis, FundamentalMetricRef, FundamentalRuleResult
from app.domain.technicals import DeterministicTechnicalAnalysis, TechnicalMetricRef, TechnicalRuleResult
from app.schemas.analyze import AnalyzeResponse, DecisionDiagnosis
from app.schemas.critique import AnalystView, CritiqueRequest, DraftRecommendation
from app.schemas.fundamentals import (
    CitedFinding,
    EvidenceChunkInput,
    EvidenceQuestionCoverage,
    FundamentalAnalysisRequest,
    FundamentalAssessmentResponse,
    FundamentalContextCoverage,
    MetricSeries,
    MetricSeriesPoint,
)
from app.schemas.technicals import (
    TechnicalAnalysisRequest,
    TechnicalAssessmentResponse,
    TechnicalContextCoverage,
    TechnicalFact,
    TechnicalFinding,
)
from app.services.critic_rules import evaluate_deterministic_objections
from app.services.critique_context import build_analyst_views
from app.services.fundamental_analyst import FundamentalAnalyst, ModelOutput
from app.services.fundamental_policy import apply_fundamental_policy
from app.trust.memo_checks import (
    TechnicalIndicatorSnapshot,
    check_memo_consistency,
    resolve_fundamental_citations,
    resolve_technical_citations,
)


class _FakeModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.model_name = "fake-gemini"

    def generate(self, prompt: str) -> ModelOutput:
        del prompt
        output = self.outputs.pop(0)
        return ModelOutput(text=output, token_usage={"total_token_count": 9})


def test_correct_memo_passes() -> None:
    memo = (
        "Trade setup: entry 100.00 to 101.00, stop loss 95.00, targets 110.00 to 115.00, "
        "risk/reward 2.0, position size 500.00. RSI 58.12, SMA50 102.5, SMA200 97.0, "
        "VIX 19.0, volume trend 1.05. Final decision: trade."
    )

    result = check_memo_consistency(
        memo,
        _response(decision="trade"),
        TechnicalIndicatorSnapshot(rsi_14=58.12, sma_50=102.5, sma_200=97.0, vix=19.0, volume_trend=1.05),
    )

    assert result.passed is True
    assert result.issues == ()


def test_wrong_stop_loss_fails() -> None:
    result = check_memo_consistency(
        "Trade with stop loss 94.00.",
        _response(decision="trade"),
    )

    assert result.passed is False
    assert [(issue.code, issue.field_name) for issue in result.issues] == [
        ("numeric_claim_mismatch", "stop_loss")
    ]


def test_wrong_decision_word_fails() -> None:
    result = check_memo_consistency(
        "This remains a watchlist candidate, not a trade yet.",
        _response(decision="trade"),
    )

    assert result.passed is False
    assert {issue.actual for issue in result.issues if issue.code == "decision_label_mismatch"} == {
        "watchlist",
        "no_trade",
    }


def test_no_trade_negation_counts_as_matching_synonym() -> None:
    result = check_memo_consistency(
        "Not a trade until the tape improves.",
        _response(decision="no_trade"),
    )

    assert result.passed is True
    assert result.issues == ()


def test_invented_rsi_value_fails() -> None:
    result = check_memo_consistency(
        "Watchlist only because RSI is 71.0.",
        _response(decision="watchlist"),
        TechnicalIndicatorSnapshot(rsi_14=58.12),
    )

    assert result.passed is False
    assert [(issue.code, issue.field_name) for issue in result.issues] == [
        ("numeric_claim_mismatch", "rsi_14")
    ]


def test_unmatched_number_is_only_a_warning() -> None:
    response = _response(decision="watchlist").model_copy(update={"stop_loss": None})

    result = check_memo_consistency("Watchlist with stop loss 95.00.", response)

    assert result.passed is True
    assert [(issue.code, issue.severity, issue.field_name) for issue in result.issues] == [
        ("unmatched_numeric_claim", "warning", "stop_loss")
    ]


def test_citation_resolution_reports_zero_unknown_ids_for_supported_response() -> None:
    request = _request()
    result = resolve_fundamental_citations(_assessment_response(request), request)

    assert result.run_id == request.run_id
    assert result.context_hash == request.context_hash
    assert result.unknown_id_count == 0
    assert result.issues == ()


def test_technical_citation_resolution_reports_zero_unknown_ids_for_supported_response() -> None:
    request = _technical_request()
    result = resolve_technical_citations(_technical_assessment_response(request), request)

    assert result.run_id == request.run_id
    assert result.context_hash == request.context_hash
    assert result.unknown_id_count == 0
    assert result.issues == ()


def test_technical_citation_resolution_flags_unknown_metric_id() -> None:
    request = _technical_request()
    response = _technical_assessment_response(request, metric_ids=["unknown_metric:2026-09-08"])

    result = resolve_technical_citations(response, request)

    assert result.unknown_id_count == 1
    assert result.issues[0].citation_type == "metric_id"
    assert result.issues[0].missing_ids == ("unknown_metric:2026-09-08",)


def _technical_request() -> TechnicalAnalysisRequest:
    return TechnicalAnalysisRequest(
        run_id="run-technical-1",
        context_hash="ctx-technical-1",
        ticker="NVDA",
        sector="ai",
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        as_of_date=date(2026, 9, 8),
        deterministic_result=DeterministicTechnicalAnalysis(
            ticker="NVDA",
            as_of_date=date(2026, 9, 8),
            score=1,
            maximum_score=5,
            rule_results=[
                TechnicalRuleResult(
                    rule_id="rsi_healthy",
                    status="passed",
                    points_awarded=1,
                    points_available=1,
                    metric_ids=["rsi_14:2026-09-08"],
                    explanation="rsi_14 55.00 within healthy range",
                )
            ],
            warnings=[],
            metrics=[
                TechnicalMetricRef(
                    metric_id="rsi_14:2026-09-08",
                    metric_name="rsi_14",
                    value=Decimal("55.00"),
                    unit="rsi_points",
                    as_of_date=date(2026, 9, 8),
                )
            ],
        ),
        facts=[
            TechnicalFact(
                metric_id="dist_sma50_pct:2026-09-08",
                name="dist_sma50_pct",
                value=Decimal("0.0523"),
                unit="pct",
                derivation="(close - sma_50) / sma_50",
            )
        ],
        series=[],
        coverage=TechnicalContextCoverage(
            bar_count=200,
            required_facts_missing=[],
            latest_bar_date=date(2026, 9, 8),
            bars_stale=False,
            series_truncated=False,
        ),
    )


def _technical_assessment_response(
    request: TechnicalAnalysisRequest,
    *,
    metric_ids: list[str] | None = None,
) -> TechnicalAssessmentResponse:
    return TechnicalAssessmentResponse(
        run_id=request.run_id,
        context_hash=request.context_hash,
        status="completed",
        overall_signal="positive",
        trend_state="uptrend",
        momentum_state="stable",
        proposed_score_adjustment=0,
        findings=[
            TechnicalFinding(
                finding_id="technical-finding-1",
                category="trend",
                direction="positive",
                materiality="high",
                claim="Price trades above both the 50-day and 200-day moving averages.",
                metric_ids=metric_ids or ["dist_sma50_pct:2026-09-08"],
            )
        ],
        contradictions=[],
        risk_flags=[],
        missing_information=[],
        summary="Technical structure is constructive.",
    )


def test_empty_evidence_short_circuits_without_increasing_confidence() -> None:
    request = _request(evidence_chunks=[])
    deterministic = _response(decision="watchlist", warnings=["no_fundamentals"], confidence=0.55)
    analyst = FundamentalAnalyst(model_port=_FakeModel([]), max_retries=0)

    assessment = analyst.assess(request)
    effect = apply_fundamental_policy(
        mode="enforced",
        deterministic_response=deterministic,
        request=request,
        assessment_result=assessment,
    )

    assert assessment.response.status == "insufficient_data"
    assert assessment.response.proposed_score_adjustment == 0
    assert assessment.validation.reason_codes == ["insufficient_evidence"]
    assert effect.applied_adjustment == 0
    assert effect.final_decision == deterministic.decision
    assert deterministic.confidence == 0.55
    assert "no_fundamentals" in deterministic.warnings


def test_weak_retrieval_stays_neutral() -> None:
    request = _request(evidence_chunks=[_evidence_chunk(10, "Thin tertiary evidence.", quality_tier="tertiary")])
    deterministic = _response(decision="watchlist", confidence=0.61)
    analyst = FundamentalAnalyst(model_port=_FakeModel([_weak_retrieval_response_json(request)]), max_retries=0)

    assessment = analyst.assess(request)
    effect = apply_fundamental_policy(
        mode="enforced",
        deterministic_response=deterministic,
        request=request,
        assessment_result=assessment,
    )

    assert assessment.validation.accepted is True
    assert assessment.response.status == "completed"
    assert assessment.response.proposed_score_adjustment == 0
    assert effect.applied_adjustment == 0
    assert effect.final_decision == "watchlist"
    assert "assessment_neutral" in effect.reason_codes


def test_contradictory_evidence_surfaces_in_fundamental_view_and_critic_objection() -> None:
    request = _request()
    response = _assessment_response(
        request,
        overall_signal="negative",
        contradictions=[
            CitedFinding(
                finding_id="contradiction-1",
                category="management_guidance",
                direction="mixed",
                materiality="high",
                claim="Management guidance conflicts with the recent filing trend.",
                metric_ids=["revenue_growth:2026-06-30"],
                chunk_ids=[10],
            )
        ],
    )
    fundamental_result = FundamentalAnalyst(model_port=_FakeModel([response.model_dump_json()]), max_retries=0).assess(
        request
    )
    analyst_views = build_analyst_views(fundamental=fundamental_result)
    critique_request = _critique_request(analyst_views=analyst_views, draft=_draft(decision="trade"))

    objections = evaluate_deterministic_objections(request=critique_request, baseline=_draft(decision="trade"))

    fundamental_view = next(view for view in analyst_views if view.agent_name == "fundamental_analyst")
    assert any("conflicts" in finding.claim for finding in fundamental_view.findings)
    assert any(objection.category == "analyst_contradiction" for objection in objections)


def _response(
    *,
    decision: str,
    warnings: list[str] | None = None,
    confidence: float = 0.72,
) -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision=decision,
        confidence=confidence,
        reasons=["deterministic"],
        warnings=warnings or [],
        engine_version="v1.rules.0",
        trace_id="trace-1",
        entry_range=(100.0, 101.0),
        stop_loss=95.0,
        take_profit=(110.0, 115.0),
        risk_reward=2.0,
        position_size_eur=500.0,
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="checklist_passed" if decision == "trade" else "score_below_trade",
            detail="baseline",
            checklist_score=8 if decision == "trade" else 7,
            debug_reason="baseline",
        ),
    )


def _request(*, evidence_chunks: list[EvidenceChunkInput] | None = None) -> FundamentalAnalysisRequest:
    chunks = evidence_chunks if evidence_chunks is not None else [_evidence_chunk(10, "Recurring demand supports growth.")]
    metric_point = MetricSeriesPoint(
        metric_id="revenue:Q2:2026-06-30:USD:rev-1",
        value=Decimal("100"),
        unit="USD",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        fiscal_year=2026,
        fiscal_period="Q2",
        form_type="10-Q",
        filed_at=date(2026, 7, 20),
        accession_number="rev-1",
        source_concept="Revenue",
        source_url="sec://rev-1",
        is_derived=False,
    )
    return FundamentalAnalysisRequest(
        run_id="run-1",
        context_hash="ctx-1",
        ticker="NVDA",
        company_name="NVIDIA",
        sector="ai",
        currency="USD",
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        as_of_date=date(2026, 9, 8),
        deterministic_result=DeterministicFundamentalAnalysis(
            ticker="NVDA",
            as_of_date=date(2026, 6, 30),
            score=2,
            maximum_score=6,
            rule_results=[
                FundamentalRuleResult(
                    rule_id="revenue_growth",
                    status="passed",
                    points_awarded=2,
                    points_available=2,
                    metric_ids=["revenue_growth:2026-06-30"],
                    explanation="revenue_growth: 0.20 > 0.15 min",
                )
            ],
            warnings=[],
            metrics=[
                FundamentalMetricRef(
                    metric_id="revenue_growth:2026-06-30",
                    name="Revenue growth",
                    value=Decimal("0.20"),
                    unit="ratio",
                    period_end=date(2026, 6, 30),
                )
            ],
        ),
        metric_series=[MetricSeries(metric_name="revenue", annual=[], quarterly=[metric_point])],
        evidence_chunks=chunks,
        coverage=FundamentalContextCoverage(
            required_metrics_present=["revenue_growth"],
            required_metrics_missing=[],
            annual_period_counts={"revenue": 0},
            quarterly_period_counts={"revenue": 1},
            source_types_present=sorted({chunk.source_type for chunk in chunks}),
            source_types_missing=[],
            newest_evidence_published_at=chunks[0].published_at if chunks else None,
            evidence_stale=False,
            evidence_truncated=False,
            metric_series_truncated=False,
            selected_chunk_count=len(chunks),
            selected_character_count=sum(len(chunk.text) for chunk in chunks),
            question_coverage=[
                EvidenceQuestionCoverage(
                    question_id="growth_sustainability",
                    chunk_ids=[chunk.chunk_id for chunk in chunks],
                    source_types=sorted({chunk.source_type for chunk in chunks}),
                    truncated=False,
                )
            ],
        ),
    )


def _assessment_response(
    request: FundamentalAnalysisRequest,
    *,
    overall_signal: str = "positive",
    contradictions: list[CitedFinding] | None = None,
) -> FundamentalAssessmentResponse:
    return FundamentalAssessmentResponse(
        run_id=request.run_id,
        context_hash=request.context_hash,
        status="completed",
        overall_signal=overall_signal,
        proposed_score_adjustment=1 if overall_signal == "positive" else -1,
        findings=[
            CitedFinding(
                finding_id="growth-1",
                category="growth_quality",
                direction="positive" if overall_signal == "positive" else "negative",
                materiality="high",
                claim="Recurring demand supports sustainable growth.",
                metric_ids=["revenue_growth:2026-06-30"],
                chunk_ids=[10],
            )
        ],
        contradictions=contradictions or [],
        material_red_flags=[],
        evidence_coverage=0.75,
        missing_information=[],
        summary="Primary evidence supports the assessment.",
    )


def _weak_retrieval_response_json(request: FundamentalAnalysisRequest) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "run_id": request.run_id,
            "context_hash": request.context_hash,
            "agent_name": "fundamental_analyst",
            "status": "completed",
            "overall_signal": "neutral",
            "proposed_score_adjustment": 0,
            "findings": [],
            "contradictions": [],
            "material_red_flags": [],
            "evidence_coverage": 0.2,
            "missing_information": ["thin_evidence"],
            "summary": "The evidence is too weak to justify an adjustment.",
        }
    )


def _evidence_chunk(chunk_id: int, text: str, *, quality_tier: str = "primary") -> EvidenceChunkInput:
    return EvidenceChunkInput(
        chunk_id=chunk_id,
        retrieval_question_id="growth_sustainability",
        source_type="filing_business",
        source_url=f"https://example.com/{chunk_id}",
        source_hash=f"hash-{chunk_id}",
        chunk_index=0,
        published_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        quality_tier=quality_tier,
        text=text,
    )


def _critique_request(
    *,
    analyst_views: list[AnalystView],
    draft: DraftRecommendation,
) -> CritiqueRequest:
    return CritiqueRequest(
        run_id="run-1",
        context_hash="ctx-1",
        ticker="NVDA",
        as_of_date=date(2026, 9, 8),
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        draft=draft,
        analyst_views=analyst_views,
        evidence_chunks=[],
        deterministic_warnings=[],
        allowed_actions=["accept", "request_revision"],
    )


def _draft(*, decision: str) -> DraftRecommendation:
    payload = {
        "decision": decision,
        "confidence": 0.72,
        "reasons": ["Reason"],
        "warnings": [],
        "memo": "",
        "source": "deterministic",
        "entry_range": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_reward": None,
        "position_size_eur": None,
    }
    if decision == "trade":
        payload.update(
            {
                "entry_range": (100.0, 101.0),
                "stop_loss": 95.0,
                "take_profit": (110.0, 115.0),
                "risk_reward": 2.0,
                "position_size_eur": 500.0,
            }
        )
    return DraftRecommendation(**payload)
