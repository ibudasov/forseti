from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.db.models import Fundamental
from app.domain.fundamentals import (
    MAX_FUNDAMENTAL_SCORE,
    DEBT_TO_EQUITY_MAX,
    EPS_TREND_MIN,
    REVENUE_GROWTH_MIN,
    DeterministicFundamentalAnalysis,
    FundamentalRuleResult,
    FundamentalSnapshotData,
    analyze_fundamentals,
)
from app.services.checklist import evaluate_checklist


def test_revenue_growth_rule_handles_threshold_edges():
    _assert_rule_threshold("revenue_growth", "revenue_growth", REVENUE_GROWTH_MIN, Decimal("0.01"))


def test_fcf_rule_handles_threshold_edges():
    _assert_rule_threshold("fcf", "fcf", Decimal("0"), Decimal("1"))


def test_debt_to_equity_rule_handles_threshold_edges():
    _assert_rule_threshold("debt_to_equity", "debt_to_equity", DEBT_TO_EQUITY_MAX, Decimal("0.01"), is_maximum=True)


def test_eps_trend_rule_handles_threshold_edges():
    _assert_rule_threshold("eps_trend", "eps_trend", EPS_TREND_MIN, Decimal("0.01"))


def test_missing_metric_is_reported_as_unknown():
    analysis = analyze_fundamentals(
        "NVDA",
        FundamentalSnapshotData(
            as_of_date=date(2025, 12, 31),
            revenue_growth=None,
            fcf=Decimal("1"),
            debt_to_equity=Decimal("0.5"),
            eps_trend=Decimal("0.1"),
            currency="USD",
        ),
    )

    revenue_growth_rule = _rule_by_id(analysis, "revenue_growth")
    assert revenue_growth_rule.status == "unknown"
    assert revenue_growth_rule.points_awarded == 0
    assert revenue_growth_rule.metric_ids == ["revenue_growth:2025-12-31"]
    assert analysis.warnings == ["missing_metric:revenue_growth"]


def test_all_pass_analysis_scores_six_of_six():
    analysis = analyze_fundamentals(
        "NVDA",
        FundamentalSnapshotData(
            as_of_date=date(2025, 12, 31),
            revenue_growth=Decimal("0.2"),
            fcf=Decimal("1000"),
            debt_to_equity=Decimal("0.5"),
            eps_trend=Decimal("0.1"),
            margins=Decimal("0.3"),
            currency="USD",
        ),
    )

    assert analysis.score == MAX_FUNDAMENTAL_SCORE
    assert analysis.maximum_score == MAX_FUNDAMENTAL_SCORE
    assert analysis.schema_version == "1.0"
    assert analysis.warnings == []
    assert [rule.status for rule in analysis.rule_results] == ["passed"] * 4
    metric_units = {metric.metric_id: metric.unit for metric in analysis.metrics}
    assert metric_units == {
        "revenue_growth:2025-12-31": "ratio",
        "fcf:2025-12-31": "USD",
        "debt_to_equity:2025-12-31": "ratio",
        "eps_trend:2025-12-31": "currency_per_share_delta",
        "margins:2025-12-31": "ratio",
    }


def test_all_fail_analysis_reports_four_explicit_failed_rules():
    analysis = analyze_fundamentals(
        "NVDA",
        FundamentalSnapshotData(
            as_of_date=date(2025, 12, 31),
            revenue_growth=Decimal("0.15"),
            fcf=Decimal("0"),
            debt_to_equity=Decimal("1.0"),
            eps_trend=Decimal("0"),
            currency="USD",
        ),
    )

    assert analysis.score == 0
    assert [rule.rule_id for rule in analysis.rule_results] == [
        "revenue_growth",
        "fcf",
        "debt_to_equity",
        "eps_trend",
    ]
    assert [rule.status for rule in analysis.rule_results] == ["failed"] * 4


def test_missing_snapshot_is_reported_explicitly():
    analysis = analyze_fundamentals(
        "NVDA",
        FundamentalSnapshotData(has_snapshot=False, currency="USD"),
    )

    assert analysis.as_of_date is None
    assert analysis.metrics == []
    assert analysis.warnings == [
        "no_fundamental_snapshot",
        "missing_metric:revenue_growth",
        "missing_metric:fcf",
        "missing_metric:debt_to_equity",
        "missing_metric:eps_trend",
    ]
    assert [rule.status for rule in analysis.rule_results] == ["unknown"] * 4


def test_present_snapshot_with_missing_metrics_is_not_missing_snapshot():
    analysis = analyze_fundamentals(
        "NVDA",
        FundamentalSnapshotData(
            has_snapshot=True,
            as_of_date=date(2025, 12, 31),
            currency="USD",
        ),
    )

    assert "no_fundamental_snapshot" not in analysis.warnings
    assert analysis.warnings == [
        "missing_metric:revenue_growth",
        "missing_metric:fcf",
        "missing_metric:debt_to_equity",
        "missing_metric:eps_trend",
    ]


def test_checklist_preserves_fundamental_rule_details():
    score, results = evaluate_checklist(
        latest_bar=None,
        fundamental=Fundamental(
            security_id=1,
            as_of_date=date(2025, 12, 31),
            revenue_growth=Decimal("0.20"),
            fcf=Decimal("1000"),
            debt_to_equity=Decimal("0.50"),
            eps_trend=Decimal("0.10"),
            margins=Decimal("0.30"),
            raw_payload={},
        ),
        technical_feature=None,
        vix_close=None,
        ticker="NVDA",
        currency="USD",
    )

    assert score == 6
    assert [(result.rule_id, result.points, result.detail) for result in results] == [
        ("revenue_growth", 2, "revenue_growth: 0.20 > 0.15 min"),
        ("fcf", 2, "fcf: 1000.00 > 0"),
        ("debt_to_equity", 1, "debt_to_equity: 0.50 < 1.00 max"),
        ("eps_trend", 1, "eps_trend: 0.10 > 0.00 min"),
    ]


def test_validation_rejects_inconsistent_score_totals():
    with pytest.raises(ValidationError, match="Score must equal the sum of awarded points"):
        DeterministicFundamentalAnalysis(
            ticker="NVDA",
            as_of_date=date(2025, 12, 31),
            score=1,
            maximum_score=6,
            rule_results=[
                FundamentalRuleResult(
                    rule_id="fcf",
                    status="passed",
                    points_awarded=2,
                    points_available=2,
                    metric_ids=["fcf:2025-12-31"],
                    explanation="fcf: 1000.00 > 0",
                )
            ],
            metrics=[],
        )


def test_validation_rejects_invalid_status_points_combinations():
    with pytest.raises(ValidationError, match="Failed and unknown rules must award zero points"):
        FundamentalRuleResult(
            rule_id="fcf",
            status="unknown",
            points_awarded=1,
            points_available=2,
            metric_ids=["fcf:2025-12-31"],
            explanation="fcf: missing",
        )


def _assert_rule_threshold(
    rule_id: str,
    metric_name: str,
    threshold: Decimal,
    delta: Decimal,
    *,
    is_maximum: bool = False,
) -> None:
    below_value = threshold + delta if is_maximum else threshold - delta
    equal_value = threshold
    above_value = threshold - delta if is_maximum else threshold + delta

    below_analysis = _analysis_with_metric(metric_name, below_value)
    equal_analysis = _analysis_with_metric(metric_name, equal_value)
    above_analysis = _analysis_with_metric(metric_name, above_value)

    below_rule = _rule_by_id(below_analysis, rule_id)
    equal_rule = _rule_by_id(equal_analysis, rule_id)
    above_rule = _rule_by_id(above_analysis, rule_id)

    if is_maximum:
        assert below_rule.status == "failed"
        assert equal_rule.status == "failed"
        assert above_rule.status == "passed"
        return

    assert below_rule.status == "failed"
    assert equal_rule.status == "failed"
    assert above_rule.status == "passed"


def _analysis_with_metric(metric_name: str, value: Decimal) -> DeterministicFundamentalAnalysis:
    values = {
        "revenue_growth": Decimal("0.2"),
        "fcf": Decimal("1000"),
        "debt_to_equity": Decimal("0.5"),
        "eps_trend": Decimal("0.1"),
        "margins": Decimal("0.3"),
    }
    values[metric_name] = value
    return analyze_fundamentals(
        "NVDA",
        FundamentalSnapshotData(
            as_of_date=date(2025, 12, 31),
            revenue_growth=values["revenue_growth"],
            fcf=values["fcf"],
            debt_to_equity=values["debt_to_equity"],
            eps_trend=values["eps_trend"],
            margins=values["margins"],
            currency="USD",
        ),
    )


def _rule_by_id(
    analysis: DeterministicFundamentalAnalysis,
    rule_id: str,
) -> FundamentalRuleResult:
    return next(rule for rule in analysis.rule_results if rule.rule_id == rule_id)
