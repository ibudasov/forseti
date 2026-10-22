from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.technicals import DeterministicTechnicalAnalysis, TechnicalMetricRef, TechnicalRuleResult
from app.schemas.analyze import AnalyzeResponse, DecisionDiagnosis
from app.schemas.fundamentals import FundamentalAgentEffect, FundamentalAgentVersionStamp
from app.schemas.technicals import (
    TechnicalAnalysisRequest,
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
    TechnicalAssessmentValidation,
    TechnicalContextCoverage,
)
from app.services.technical_policy import apply_technical_policy

_MODES = ("off", "shadow", "enforced")
_BASELINE_DECISIONS = ("trade", "watchlist", "no_trade")
_ADJUSTMENTS = (-2, -1, 0)
_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}


def test_assessment_invalid_reason_code_zeroes_adjustment():
    response = _response(decision="watchlist", confidence=0.5, checklist_score=7)
    request = _request()
    assessment = _assessment(request, adjustment=-1, accepted=False, reason_codes=["provider_error:timeout"])

    effect = apply_technical_policy(
        mode="enforced",
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.accepted is False
    assert effect.applied_adjustment == 0
    assert effect.final_decision == "watchlist"
    assert effect.reason_codes == ["provider_error:timeout", "assessment_invalid"]


def test_assessment_neutral_reason_code():
    response = _response(decision="watchlist", confidence=0.5, checklist_score=7)
    request = _request()
    assessment = _assessment(request, adjustment=0, accepted=True)

    effect = apply_technical_policy(
        mode="enforced",
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.applied_adjustment == 0
    assert effect.reason_codes == ["assessment_neutral"]
    assert effect.decision_changed is False


def test_negative_adjustment_is_applied():
    response = _response(decision="trade", confidence=0.9, checklist_score=8)
    request = _request()
    assessment = _assessment(request, adjustment=-1, accepted=True)

    effect = apply_technical_policy(
        mode="enforced",
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.applied_adjustment == -1
    assert "adjustment_applied" in effect.reason_codes


def test_positive_adjustment_is_defensively_rejected():
    response = _response(decision="watchlist", confidence=0.5, checklist_score=7)
    request = _request()
    bypassed_response = TechnicalAssessmentResponse.model_construct(
        schema_version="1.0",
        run_id=request.run_id,
        context_hash=request.context_hash,
        agent_name="technical_analyst",
        status="completed",
        overall_signal="positive",
        trend_state="uptrend",
        momentum_state="strengthening",
        proposed_score_adjustment=1,
        findings=[],
        contradictions=[],
        risk_flags=[],
        missing_information=[],
        summary="Bypassed validation to exercise the defensive clamp.",
    )
    assessment = TechnicalAssessmentResult.model_construct(
        response=bypassed_response,
        validation=TechnicalAssessmentValidation(accepted=True, reason_codes=[]),
        raw_output="",
        latency_ms=0.0,
        token_usage={},
        model_name="fake",
        prompt_version="v1",
    )

    effect = apply_technical_policy(
        mode="enforced",
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.raw_adjustment == 1
    assert effect.applied_adjustment == 0
    assert "positive_adjustment_rejected" in effect.reason_codes
    assert effect.decision_changed is False


def test_shadow_mode_never_changes_the_decision():
    response = _response(decision="trade", confidence=0.9, checklist_score=8)
    request = _request()
    assessment = _assessment(request, adjustment=-1, accepted=True)

    effect = apply_technical_policy(
        mode="shadow",
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.final_decision == "trade"
    assert effect.decision_changed is False
    assert "shadow_mode" in effect.reason_codes
    assert effect.final_confidence == effect.baseline_confidence


def test_enforced_trade_downgrade_clears_confidence_increase_and_crosses_threshold():
    response = _response(decision="trade", confidence=0.72, checklist_score=8)
    request = _request()
    assessment = _assessment(request, adjustment=-1, accepted=True)

    effect = apply_technical_policy(
        mode="enforced",
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.counterfactual_decision == "watchlist"
    assert effect.final_decision == "watchlist"
    assert effect.decision_changed is True
    assert effect.final_confidence <= effect.baseline_confidence


def test_enforced_mode_with_hard_blocker_gives_no_change():
    response = _response(
        decision="watchlist",
        confidence=0.5,
        checklist_score=8,
        warnings=["stale_price_data"],
    )
    request = _request()
    assessment = _assessment(request, adjustment=-1, accepted=True)

    effect = apply_technical_policy(
        mode="enforced",
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.final_decision == "watchlist"
    assert effect.decision_changed is False
    assert "hard_blocker_present" in effect.reason_codes


def test_no_trade_baseline_stays_no_trade_even_with_high_checklist_score():
    # A risk-math downgrade can leave checklist_score high while the decision
    # is already no_trade; the counterfactual must still be downgrade-only.
    response = _response(decision="no_trade", confidence=0.1, checklist_score=9)
    request = _request()
    assessment = _assessment(request, adjustment=-1, accepted=True)

    effect = apply_technical_policy(
        mode="enforced",
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.final_decision == "no_trade"
    assert effect.counterfactual_decision == "no_trade"
    assert effect.decision_changed is False


def test_baseline_total_score_prefers_fundamental_adjusted_score():
    response = _response(decision="watchlist", confidence=0.5, checklist_score=7)
    response.fundamental_agent_effect = _fundamental_effect(adjusted_total_score=9, decision="watchlist")
    request = _request()
    assessment = _assessment(request, adjustment=-1, accepted=True)

    effect = apply_technical_policy(
        mode="shadow",
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert effect.baseline_total_score == 9
    assert effect.adjusted_total_score == 8


@pytest.mark.parametrize("baseline_decision", _BASELINE_DECISIONS)
@pytest.mark.parametrize("adjustment", _ADJUSTMENTS)
@pytest.mark.parametrize("mode", _MODES)
def test_final_decision_never_outranks_post_fundamental_decision(baseline_decision, adjustment, mode):
    checklist_score = {"trade": 8, "watchlist": 6, "no_trade": 3}[baseline_decision]
    response = _response(decision=baseline_decision, confidence=0.6, checklist_score=checklist_score)
    request = _request()
    assessment = _assessment(request, adjustment=adjustment, accepted=True)

    effect = apply_technical_policy(
        mode=mode,
        deterministic_response=response,
        post_fundamental_response=response,
        request=request,
        assessment_result=assessment,
    )

    assert _RANK[effect.final_decision] <= _RANK[baseline_decision]
    if effect.final_decision == "trade":
        assert response.entry_range is not None
    else:
        # The policy effect itself never mutates `response`; only the
        # workflow's `_apply_technical_effect_to_response` clears risk
        # fields. Here we only assert the effect's own decision/confidence
        # invariants, never increasing confidence.
        assert effect.final_confidence <= effect.baseline_confidence


def _response(
    *,
    decision: str,
    confidence: float,
    checklist_score: int,
    warnings: list[str] | None = None,
) -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision=decision,
        confidence=confidence,
        reasons=["deterministic"],
        warnings=warnings or [],
        engine_version="v1.rules.0",
        trace_id="trace-1",
        entry_range=(100.0, 101.0) if decision == "trade" else None,
        stop_loss=95.0 if decision == "trade" else None,
        take_profit=(110.0, 115.0) if decision == "trade" else None,
        risk_reward=2.0 if decision == "trade" else None,
        position_size_eur=500.0 if decision == "trade" else None,
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="checklist_passed" if checklist_score >= 8 else "score_below_trade",
            detail="baseline",
            checklist_score=checklist_score,
            debug_reason="baseline",
        ),
    )


def _fundamental_effect(*, adjusted_total_score: int, decision: str) -> FundamentalAgentEffect:
    return FundamentalAgentEffect(
        mode="enforced",
        accepted=True,
        assessment_status="completed",
        overall_signal="neutral",
        reason_codes=[],
        missing_information=[],
        source_types_missing=[],
        raw_adjustment=0,
        applied_adjustment=0,
        baseline_fundamental_score=2,
        adjusted_fundamental_score=2,
        baseline_total_score=adjusted_total_score,
        adjusted_total_score=adjusted_total_score,
        baseline_decision=decision,
        counterfactual_decision=decision,
        final_decision=decision,
        decision_changed=False,
        versions=FundamentalAgentVersionStamp(
            context_schema_version="1.0",
            assessment_schema_version="1.0",
            prompt_version="v1",
            model_name="fake",
            policy_version="1.0",
        ),
    )


def _request() -> TechnicalAnalysisRequest:
    return TechnicalAnalysisRequest(
        run_id="run-1",
        context_hash="ctx-1",
        ticker="NVDA",
        sector="ai",
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        as_of_date=date(2026, 9, 8),
        deterministic_result=DeterministicTechnicalAnalysis(
            ticker="NVDA",
            as_of_date=date(2026, 9, 8),
            score=2,
            maximum_score=5,
            rule_results=[
                TechnicalRuleResult(
                    rule_id="rsi_healthy",
                    status="passed",
                    points_awarded=1,
                    points_available=1,
                    metric_ids=["rsi_14:2026-09-08"],
                    explanation="rsi_14 55.00 within healthy range",
                ),
                TechnicalRuleResult(
                    rule_id="volume_trend",
                    status="passed",
                    points_awarded=1,
                    points_available=1,
                    metric_ids=["volume_trend:2026-09-08"],
                    explanation="volume_trend 1.20 >= 1.0 min",
                ),
            ],
            warnings=[],
            metrics=[
                TechnicalMetricRef(
                    metric_id="rsi_14:2026-09-08",
                    metric_name="rsi_14",
                    value=Decimal("55.00"),
                    unit="rsi_points",
                    as_of_date=date(2026, 9, 8),
                ),
                TechnicalMetricRef(
                    metric_id="volume_trend:2026-09-08",
                    metric_name="volume_trend",
                    value=Decimal("1.20"),
                    unit="ratio",
                    as_of_date=date(2026, 9, 8),
                ),
            ],
        ),
        facts=[],
        series=[],
        coverage=TechnicalContextCoverage(
            bar_count=200,
            required_facts_missing=[],
            latest_bar_date=date(2026, 9, 8),
            bars_stale=False,
            series_truncated=False,
        ),
    )


def _assessment(
    request: TechnicalAnalysisRequest,
    *,
    adjustment: int,
    accepted: bool,
    reason_codes: list[str] | None = None,
) -> TechnicalAssessmentResult:
    response = TechnicalAssessmentResponse(
        run_id=request.run_id,
        context_hash=request.context_hash,
        status="completed",
        overall_signal="negative" if adjustment < 0 else "neutral",
        trend_state="range",
        momentum_state="weakening" if adjustment < 0 else "stable",
        proposed_score_adjustment=adjustment,
        findings=[],
        contradictions=[],
        risk_flags=[],
        missing_information=[],
        summary="Assessment summary.",
    )
    return TechnicalAssessmentResult(
        response=response,
        validation=TechnicalAssessmentValidation(
            accepted=accepted,
            reason_codes=reason_codes or [],
        ),
        raw_output="",
        latency_ms=0.0,
        token_usage={},
        model_name="fake",
        prompt_version="v1",
    )
