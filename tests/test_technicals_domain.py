"""Unit tests for the deterministic technical baseline contract."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.db.models import PriceBar, TechnicalFeature
from app.domain.technicals import (
    RSI_HEALTHY_MAX,
    RSI_HEALTHY_MIN,
    TECHNICAL_MAXIMUM_SCORE,
    VIX_CALM_MAX,
    DeterministicTechnicalAnalysis,
    TechnicalRuleResult,
    TechnicalVetoState,
    analyze_technicals,
)

RULE_IDS = [
    "close_vs_sma50",
    "close_vs_sma200",
    "rsi_healthy",
    "volume_trend",
    "vix_calm",
]


def _bar(close: Decimal = Decimal("100"), bar_date: date = date(2026, 1, 1)) -> PriceBar:
    return PriceBar(
        security_id=1,
        bar_date=bar_date,
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=1_000_000,
    )


def _feature(
    as_of_date: date = date(2026, 1, 1),
    rsi_14: Decimal | None = Decimal("55"),
    sma_50: Decimal | None = Decimal("90"),
    sma_200: Decimal | None = Decimal("80"),
    volume_trend: Decimal | None = Decimal("1.5"),
) -> TechnicalFeature:
    return TechnicalFeature(
        security_id=1,
        as_of_date=as_of_date,
        rsi_14=rsi_14,
        sma_50=sma_50,
        sma_200=sma_200,
        volume_trend=volume_trend,
    )


def _rule_by_id(analysis: DeterministicTechnicalAnalysis, rule_id: str) -> TechnicalRuleResult:
    for result in analysis.rule_results:
        if result.rule_id == rule_id:
            return result
    raise AssertionError(f"rule {rule_id} not found")


def test_all_rules_pass_scores_five_of_five():
    analysis = analyze_technicals(
        _bar(close=Decimal("100")),
        _feature(),
        vix_close=Decimal("15"),
        ticker="NVDA",
    )

    assert analysis.score == TECHNICAL_MAXIMUM_SCORE
    assert analysis.maximum_score == TECHNICAL_MAXIMUM_SCORE
    assert analysis.schema_version == "1.0"
    assert analysis.warnings == []
    assert [result.status for result in analysis.rule_results] == ["passed"] * 5
    assert [result.rule_id for result in analysis.rule_results] == RULE_IDS


def test_score_always_equals_sum_of_points_awarded():
    analysis = analyze_technicals(
        _bar(close=Decimal("50")),
        _feature(rsi_14=Decimal("80")),
        vix_close=Decimal("40"),
        ticker="NVDA",
    )

    assert analysis.score == sum(result.points_awarded for result in analysis.rule_results)


def test_all_five_rules_are_always_present_even_with_no_inputs():
    analysis = analyze_technicals(None, None, None, ticker="NVDA")

    assert [result.rule_id for result in analysis.rule_results] == RULE_IDS
    assert all(result.status == "unknown" for result in analysis.rule_results)
    assert analysis.score == 0
    assert "no_technical_features" in analysis.warnings
    assert "no_vix" in analysis.warnings


def test_missing_technical_feature_marks_feature_based_rules_unknown():
    analysis = analyze_technicals(_bar(), None, Decimal("15"), ticker="NVDA")

    for rule_id in ["close_vs_sma50", "close_vs_sma200", "rsi_healthy", "volume_trend"]:
        assert _rule_by_id(analysis, rule_id).status == "unknown"
    assert _rule_by_id(analysis, "vix_calm").status == "passed"
    assert "no_technical_features" in analysis.warnings


def test_missing_latest_bar_marks_only_sma_rules_unknown():
    analysis = analyze_technicals(None, _feature(), Decimal("15"), ticker="NVDA")

    assert _rule_by_id(analysis, "close_vs_sma50").status == "unknown"
    assert _rule_by_id(analysis, "close_vs_sma200").status == "unknown"
    assert _rule_by_id(analysis, "rsi_healthy").status == "passed"
    assert _rule_by_id(analysis, "volume_trend").status == "passed"


def test_missing_single_value_only_marks_that_rule_unknown():
    analysis = analyze_technicals(
        _bar(),
        _feature(sma_50=None),
        Decimal("15"),
        ticker="NVDA",
    )

    assert _rule_by_id(analysis, "close_vs_sma50").status == "unknown"
    assert _rule_by_id(analysis, "close_vs_sma200").status == "passed"
    assert analysis.warnings == ["missing_metric:close_vs_sma50"]


def test_missing_vix_marks_only_vix_rule_unknown():
    analysis = analyze_technicals(_bar(), _feature(), None, ticker="NVDA")

    assert _rule_by_id(analysis, "vix_calm").status == "unknown"
    assert _rule_by_id(analysis, "close_vs_sma50").status == "passed"
    assert "no_vix" in analysis.warnings


@pytest.mark.parametrize(
    "rsi_value,expected_status",
    [
        (RSI_HEALTHY_MIN, "passed"),
        (RSI_HEALTHY_MAX, "passed"),
        (RSI_HEALTHY_MIN - Decimal("0.01"), "failed"),
        (RSI_HEALTHY_MAX + Decimal("0.01"), "failed"),
    ],
)
def test_rsi_healthy_rule_handles_threshold_edges(rsi_value, expected_status):
    analysis = analyze_technicals(
        _bar(),
        _feature(rsi_14=rsi_value),
        Decimal("15"),
        ticker="NVDA",
    )

    assert _rule_by_id(analysis, "rsi_healthy").status == expected_status


@pytest.mark.parametrize(
    "vix_value,expected_status",
    [
        (VIX_CALM_MAX - Decimal("0.01"), "passed"),
        (VIX_CALM_MAX, "failed"),
        (VIX_CALM_MAX + Decimal("1"), "failed"),
    ],
)
def test_vix_calm_rule_handles_threshold_edges(vix_value, expected_status):
    analysis = analyze_technicals(_bar(), _feature(), vix_value, ticker="NVDA")

    assert _rule_by_id(analysis, "vix_calm").status == expected_status


def test_passed_rule_cites_non_empty_metric_ids_with_non_null_values():
    analysis = analyze_technicals(_bar(), _feature(), Decimal("15"), ticker="NVDA")

    metric_values = {metric.metric_id: metric.value for metric in analysis.metrics}
    for result in analysis.rule_results:
        if result.status != "passed":
            continue
        assert result.metric_ids
        for metric_id in result.metric_ids:
            assert metric_values[metric_id] is not None


def test_metric_id_format_is_metric_name_colon_as_of_date():
    analysis = analyze_technicals(
        _bar(bar_date=date(2026, 1, 1)),
        _feature(as_of_date=date(2026, 1, 1)),
        Decimal("15"),
        ticker="NVDA",
    )

    metric_ids = {metric.metric_id for metric in analysis.metrics}
    assert "close:2026-01-01" in metric_ids
    assert "sma_50:2026-01-01" in metric_ids
    assert "rsi_14:2026-01-01" in metric_ids


def test_metric_id_falls_back_to_unknown_without_any_date_source():
    analysis = analyze_technicals(None, None, Decimal("15"), ticker="NVDA")

    metric_ids = {metric.metric_id for metric in analysis.metrics}
    assert metric_ids == {"vix_close:unknown"}


def test_as_of_date_prefers_technical_feature_then_bar_then_none():
    analysis = analyze_technicals(
        _bar(bar_date=date(2025, 6, 1)),
        _feature(as_of_date=date(2026, 1, 1)),
        None,
        ticker="NVDA",
    )
    assert analysis.as_of_date == date(2026, 1, 1)

    analysis_bar_only = analyze_technicals(_bar(bar_date=date(2025, 6, 1)), None, None, ticker="NVDA")
    assert analysis_bar_only.as_of_date == date(2025, 6, 1)

    analysis_none = analyze_technicals(None, None, None, ticker="NVDA")
    assert analysis_none.as_of_date is None


def test_veto_states_default_to_empty_and_are_passed_through():
    analysis = analyze_technicals(_bar(), _feature(), Decimal("15"), ticker="NVDA")
    assert analysis.veto_states == []

    veto = TechnicalVetoState(veto_id="rsi_overbought", triggered=False, metric_ids=["rsi_14:2026-01-01"])
    analysis_with_vetoes = analyze_technicals(
        _bar(), _feature(), Decimal("15"), ticker="NVDA", veto_states=[veto]
    )
    assert analysis_with_vetoes.veto_states == [veto]


def test_passed_rule_cannot_cite_a_null_metric():
    with pytest.raises(ValidationError):
        DeterministicTechnicalAnalysis(
            ticker="NVDA",
            as_of_date=date(2026, 1, 1),
            score=1,
            rule_results=[
                TechnicalRuleResult(
                    rule_id="rsi_healthy",
                    status="passed",
                    points_awarded=1,
                    points_available=1,
                    metric_ids=["rsi_14:2026-01-01"],
                    explanation="rsi_14: 55.00 in [45, 65]",
                )
            ],
            metrics=[],
        )


def test_score_above_maximum_is_rejected():
    with pytest.raises(ValidationError):
        DeterministicTechnicalAnalysis(
            ticker="NVDA",
            as_of_date=date(2026, 1, 1),
            score=6,
            rule_results=[],
            metrics=[],
        )
