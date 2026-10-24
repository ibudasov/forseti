"""Offline unit tests for `scripts.technical_backtest`.

Every case here uses synthetic in-memory bar series -- no database, no
network, no model calls -- matching the module's own "pure replay"
contract.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts.technical_backtest import (
    Bar,
    ShadowRunRecord,
    build_backtest_report,
    forward_return,
    render_markdown,
)


def _daily_bars(start: date, closes: list[float]) -> list[Bar]:
    """Build one simulated trading day per close, skipping weekends."""
    bars: list[Bar] = []
    current_date = start
    for close in closes:
        while current_date.weekday() >= 5:
            current_date += timedelta(days=1)
        bars.append(Bar(bar_date=current_date, close=close))
        current_date += timedelta(days=1)
    return bars


def test_forward_return_computes_simple_percentage_change():
    bars = _daily_bars(date(2026, 1, 5), [100.0] * 20 + [110.0])

    result = forward_return(bars, as_of_date=bars[0].bar_date, horizon_trading_days=20)

    assert result == pytest.approx(0.10)


def test_forward_return_anchors_on_last_bar_on_or_before_as_of_date():
    friday = date(2026, 1, 2)
    monday = date(2026, 1, 5)
    saturday = date(2026, 1, 3)  # weekend, no bar: as_of_date must still anchor on Friday's bar.
    bars = [Bar(bar_date=friday, close=100.0)] + [Bar(bar_date=monday, close=100.0)] * 19 + [
        Bar(bar_date=monday + timedelta(days=28), close=110.0)
    ]

    result = forward_return(bars, as_of_date=saturday, horizon_trading_days=20)

    assert result == pytest.approx(0.10)


def test_forward_return_is_none_when_as_of_date_precedes_all_bars():
    bars = _daily_bars(date(2026, 1, 5), [100.0] * 25)

    result = forward_return(bars, as_of_date=date(2025, 1, 1), horizon_trading_days=20)

    assert result is None


def test_forward_return_is_none_when_horizon_exceeds_available_bars():
    bars = _daily_bars(date(2026, 1, 5), [100.0] * 10)

    result = forward_return(bars, as_of_date=bars[0].bar_date, horizon_trading_days=20)

    assert result is None


def test_forward_return_is_none_for_zero_anchor_close():
    bars = _daily_bars(date(2026, 1, 5), [0.0] + [100.0] * 20)

    result = forward_return(bars, as_of_date=bars[0].bar_date, horizon_trading_days=20)

    assert result is None


def test_shadow_run_record_is_downgrade_compares_decision_rank():
    downgrade = ShadowRunRecord(
        run_id="run-1",
        ticker="NVDA",
        as_of_date=date(2026, 1, 5),
        baseline_decision="trade",
        counterfactual_decision="watchlist",
    )
    neutral = ShadowRunRecord(
        run_id="run-2",
        ticker="NVDA",
        as_of_date=date(2026, 1, 5),
        baseline_decision="trade",
        counterfactual_decision="trade",
    )

    assert downgrade.is_downgrade is True
    assert neutral.is_downgrade is False


def test_build_backtest_report_splits_cohorts_and_counts_exclusions():
    bars = _daily_bars(date(2026, 1, 5), [100.0] * 20 + [90.0])  # -10% forward return
    neutral_bars = _daily_bars(date(2026, 1, 5), [100.0] * 20 + [105.0])  # +5% forward return
    bars_by_ticker = {"NVDA": bars, "LMT": neutral_bars}

    records = [
        ShadowRunRecord(
            run_id="run-downgrade",
            ticker="NVDA",
            as_of_date=bars[0].bar_date,
            baseline_decision="trade",
            counterfactual_decision="watchlist",
        ),
        ShadowRunRecord(
            run_id="run-neutral",
            ticker="LMT",
            as_of_date=neutral_bars[0].bar_date,
            baseline_decision="trade",
            counterfactual_decision="trade",
        ),
        ShadowRunRecord(
            run_id="run-excluded",
            ticker="UNKNOWN",
            as_of_date=date(2026, 1, 5),
            baseline_decision="trade",
            counterfactual_decision="trade",
        ),
    ]

    report = build_backtest_report(records, bars_by_ticker, horizon_trading_days=20)

    assert report.downgrade.sample_size == 1
    assert report.downgrade.mean_forward_return == pytest.approx(-0.10)
    assert report.neutral.sample_size == 1
    assert report.neutral.mean_forward_return == pytest.approx(0.05)
    assert report.excluded_insufficient_bars_count == 1


def test_build_backtest_report_cohorts_are_empty_when_no_record_has_bars():
    report = build_backtest_report(
        [
            ShadowRunRecord(
                run_id="run-1",
                ticker="UNKNOWN",
                as_of_date=date(2026, 1, 5),
                baseline_decision="trade",
                counterfactual_decision="trade",
            )
        ],
        bars_by_ticker={},
    )

    assert report.downgrade.sample_size == 0
    assert report.downgrade.mean_forward_return is None
    assert report.neutral.sample_size == 0
    assert report.excluded_insufficient_bars_count == 1


def test_render_markdown_reports_sample_sizes_without_significance_claims():
    bars = _daily_bars(date(2026, 1, 5), [100.0] * 20 + [90.0])
    report = build_backtest_report(
        [
            ShadowRunRecord(
                run_id="run-1",
                ticker="NVDA",
                as_of_date=bars[0].bar_date,
                baseline_decision="trade",
                counterfactual_decision="watchlist",
            )
        ],
        {"NVDA": bars},
        horizon_trading_days=20,
    )

    markdown = render_markdown(report)

    assert "technical_would_downgrade" in markdown
    assert "technical_neutral" in markdown
    assert "n/a" in markdown  # neutral cohort has no samples
    assert "no p-value or significance claim is made" in markdown
