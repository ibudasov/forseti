from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.fundamentals import DeterministicFundamentalAnalysis, FundamentalMetricRef, FundamentalRuleResult
from app.schemas.analyze import AnalyzeResponse, DecisionDiagnosis
from app.schemas.fundamentals import (
    CitedFinding,
    EvidenceChunkInput,
    EvidenceQuestionCoverage,
    FundamentalAnalysisRequest,
    FundamentalAssessmentResponse,
    FundamentalAssessmentResult,
    FundamentalAssessmentValidation,
    FundamentalContextCoverage,
)
from app.services.fundamental_policy import apply_fundamental_policy


def test_shadow_mode_records_trade_counterfactual_without_changing_response():
    response = _response(decision="watchlist", checklist_score=7, warnings=[])
    request = _request()
    assessment = _assessment(request, adjustment=1, accepted=True)

    effect = apply_fundamental_policy(
        mode="shadow",
        deterministic_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.counterfactual_decision == "trade"
    assert effect.final_decision == "watchlist"
    assert effect.decision_changed is False
    assert effect.reason_codes == ["adjustment_applied", "shadow_mode"]


def test_enforced_mode_applies_negative_adjustment_across_threshold():
    response = _response(decision="trade", checklist_score=8, warnings=[])
    request = _request()
    assessment = _assessment(request, adjustment=-1, accepted=True)

    effect = apply_fundamental_policy(
        mode="enforced",
        deterministic_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.counterfactual_decision == "watchlist"
    assert effect.final_decision == "watchlist"
    assert effect.decision_changed is True


def test_hard_blocker_prevents_positive_promotion():
    response = _response(decision="watchlist", checklist_score=7, warnings=["stale_price_data"])
    request = _request()
    assessment = _assessment(request, adjustment=2, accepted=True)

    effect = apply_fundamental_policy(
        mode="enforced",
        deterministic_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.applied_adjustment == 0
    assert effect.final_decision == "watchlist"
    assert "hard_blocker_prevents_promotion" in effect.reason_codes


def test_invalid_assessment_is_neutral():
    response = _response(decision="watchlist", checklist_score=7, warnings=[])
    request = _request()
    assessment = _assessment(request, adjustment=1, accepted=False, reason_codes=["unknown_chunk_citation"])

    effect = apply_fundamental_policy(
        mode="enforced",
        deterministic_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.accepted is False
    assert effect.applied_adjustment == 0
    assert effect.final_decision == "watchlist"
    assert effect.reason_codes == ["unknown_chunk_citation", "assessment_invalid"]


def test_duplicate_citations_reject_the_adjustment():
    response = _response(decision="watchlist", checklist_score=7, warnings=[])
    request = _request()
    duplicated = FundamentalAssessmentResponse(
        run_id=request.run_id,
        context_hash=request.context_hash,
        status="completed",
        overall_signal="positive",
        proposed_score_adjustment=1,
        findings=[
            CitedFinding(
                finding_id="a",
                category="growth_quality",
                direction="positive",
                materiality="high",
                claim="A",
                metric_ids=["revenue_growth:2026-06-30"],
                chunk_ids=[10],
            ),
            CitedFinding(
                finding_id="b",
                category="profitability",
                direction="positive",
                materiality="medium",
                claim="B",
                metric_ids=["revenue_growth:2026-06-30"],
                chunk_ids=[10],
            ),
        ],
        contradictions=[],
        material_red_flags=[],
        evidence_coverage=0.5,
        missing_information=[],
        summary="Duplicate evidence should be rejected.",
    )
    assessment = FundamentalAssessmentResult(
        response=duplicated,
        validation=FundamentalAssessmentValidation(accepted=True, reason_codes=[]),
        raw_output="",
        latency_ms=0.0,
        token_usage={},
        model_name="fake",
        prompt_version="v1",
    )

    effect = apply_fundamental_policy(
        mode="enforced",
        deterministic_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.accepted is False
    assert effect.applied_adjustment == 0
    assert "duplicate_evidence_detected" in effect.reason_codes


def _response(*, decision: str, checklist_score: int, warnings: list[str]) -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision=decision,
        confidence=0.5,
        reasons=["deterministic"],
        warnings=warnings,
        engine_version="v1.rules.0",
        trace_id="trace-1",
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="checklist_passed" if checklist_score >= 8 else "score_below_trade",
            detail="baseline",
            checklist_score=checklist_score,
            debug_reason="baseline",
        ),
    )


def _request() -> FundamentalAnalysisRequest:
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
        metric_series=[],
        evidence_chunks=[
            EvidenceChunkInput(
                chunk_id=10,
                retrieval_question_id="growth_sustainability",
                source_type="filing_business",
                source_url="https://example.com/10",
                source_hash="hash-10",
                chunk_index=0,
                published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
                quality_tier="primary",
                text="Recurring demand supports growth.",
            )
        ],
        coverage=FundamentalContextCoverage(
            required_metrics_present=["revenue_growth"],
            required_metrics_missing=[],
            annual_period_counts={},
            quarterly_period_counts={},
            source_types_present=["filing_business"],
            source_types_missing=[],
            newest_evidence_published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            evidence_stale=False,
            evidence_truncated=False,
            metric_series_truncated=False,
            selected_chunk_count=1,
            selected_character_count=32,
            question_coverage=[
                EvidenceQuestionCoverage(
                    question_id="growth_sustainability",
                    chunk_ids=[10],
                    source_types=["filing_business"],
                    truncated=False,
                )
            ],
        ),
    )


def _assessment(
    request: FundamentalAnalysisRequest,
    *,
    adjustment: int,
    accepted: bool,
    reason_codes: list[str] | None = None,
) -> FundamentalAssessmentResult:
    response = FundamentalAssessmentResponse(
        run_id=request.run_id,
        context_hash=request.context_hash,
        status="completed",
        overall_signal="positive" if adjustment > 0 else "negative" if adjustment < 0 else "neutral",
        proposed_score_adjustment=adjustment,
        findings=[
            CitedFinding(
                finding_id="f1",
                category="growth_quality",
                direction="positive" if adjustment >= 0 else "negative",
                materiality="high",
                claim="Well-supported claim.",
                metric_ids=["revenue_growth:2026-06-30"],
                chunk_ids=[10],
            )
        ],
        contradictions=[],
        material_red_flags=[],
        evidence_coverage=0.5,
        missing_information=[],
        summary="Assessment summary.",
    )
    return FundamentalAssessmentResult(
        response=response,
        validation=FundamentalAssessmentValidation(
            accepted=accepted,
            reason_codes=reason_codes or [],
        ),
        raw_output="",
        latency_ms=0.0,
        token_usage={},
        model_name="fake",
        prompt_version="v1",
    )
