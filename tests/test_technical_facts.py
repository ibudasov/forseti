"""Hand-computed unit tests for every derived technical fact calculator.

Each calculator in ``app.domain.technical_facts`` is a pure function. These
tests verify known, hand-computed (or exactly reproducible) values on small
synthetic series, plus edge cases: fewer than 60 bars, zero volume, a flat
series (ATR 0), and a missing ``TechnicalFeature``.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.db.models import PriceBar
from app.domain.technical_facts import (
    build_technical_facts,
    calculate_atr_pct,
    calculate_days_since_sma_cross,
    calculate_days_to_earnings,
    calculate_dist_pct,
    calculate_extreme,
    calculate_return_pct,
    calculate_rsi_change,
    calculate_rsi_price_divergence,
    calculate_sma_above,
    calculate_sma_slope_pct,
    calculate_up_volume_ratio,
)


def _decimals(values: list) -> list[Decimal]:
    return [Decimal(str(value)) for value in values]


def test_calculate_dist_pct_hand_computed():
    assert calculate_dist_pct(Decimal("110"), Decimal("100")) == Decimal("0.1000")


def test_calculate_dist_pct_missing_or_zero_reference_is_none():
    assert calculate_dist_pct(Decimal("110"), None) is None
    assert calculate_dist_pct(Decimal("110"), Decimal("0")) is None


def test_calculate_sma_slope_pct_hand_computed():
    # sma_50(=5)_now = mean([20,21,22,23,24]) = 22; sma_then (10 bars earlier,
    # here lookback=3) = mean([17,18,19,20,21]) = 19. slope = (22-19)/19.
    closes = _decimals(range(10, 25))  # 15 values: 10..24
    slope = calculate_sma_slope_pct(closes, window=5, lookback_days=3)
    assert slope == Decimal("0.1579")


def test_calculate_sma_slope_pct_insufficient_history_is_none():
    closes = _decimals([1, 2, 3])
    assert calculate_sma_slope_pct(closes, window=5, lookback_days=10) is None


def test_calculate_sma_above_hand_computed():
    assert calculate_sma_above(Decimal("5"), Decimal("3")) is True
    assert calculate_sma_above(Decimal("3"), Decimal("5")) is False
    assert calculate_sma_above(Decimal("3"), Decimal("3")) is False


def test_calculate_sma_above_missing_input_is_none():
    assert calculate_sma_above(None, Decimal("3")) is None
    assert calculate_sma_above(Decimal("3"), None) is None


def test_calculate_days_since_sma_cross_hand_computed():
    # short_window=2, long_window=4. (sma2 - sma4) flips sign between index 7
    # (-1.0) and index 8 (-0.25 -> +1.25), i.e. 3 bars before the last bar.
    closes = _decimals([10, 10, 10, 10, 10, 9, 8, 7, 9, 11, 13, 15])
    assert calculate_days_since_sma_cross(closes, short_window=2, long_window=4) == 3


def test_calculate_days_since_sma_cross_no_cross_is_none():
    closes = _decimals(range(1, 20))  # steadily increasing: sma2 > sma4 always
    assert calculate_days_since_sma_cross(closes, short_window=2, long_window=4) is None


def test_calculate_days_since_sma_cross_insufficient_history_is_none():
    closes = _decimals([1, 2, 3])
    assert calculate_days_since_sma_cross(closes, short_window=2, long_window=4) is None


def test_calculate_rsi_change_hand_computed():
    closes = _decimals(
        [50, 51, 52, 53, 54, 53, 52, 51, 50, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60]
    )
    # rsi_now (full 21-bar history) = 77.1054, rsi 5 bars earlier = 66.8367.
    assert calculate_rsi_change(closes, period=14, lookback_days=5) == Decimal("10.2687")


def test_calculate_rsi_change_insufficient_history_is_none():
    closes = _decimals(range(1, 10))
    assert calculate_rsi_change(closes, period=14, lookback_days=5) is None


def test_calculate_rsi_price_divergence_true_when_price_and_rsi_disagree():
    # Over the last 5 bars, the close makes a new high (122, the window max)
    # while RSI peaks on the first bar of the window (59.3784) and is lower
    # (59.2077) on the last bar - a textbook bearish divergence.
    closes = _decimals(
        [
            100, 98, 101, 97, 102, 96, 103, 95, 104, 94, 105, 93, 106, 92, 107,
            108, 111, 112, 114, 119, 121, 118, 116, 120, 122,
        ]
    )
    assert calculate_rsi_price_divergence(closes, period=14, lookback_days=5) is True


def test_calculate_rsi_price_divergence_false_when_aligned():
    closes = _decimals(range(100, 141))  # monotonic uptrend: both make new highs together
    assert calculate_rsi_price_divergence(closes, period=14, lookback_days=20) is False


def test_calculate_rsi_price_divergence_insufficient_history_is_none():
    closes = _decimals(range(1, 10))
    assert calculate_rsi_price_divergence(closes, period=14, lookback_days=20) is None


def test_calculate_atr_pct_hand_computed():
    assert calculate_atr_pct(Decimal("2"), Decimal("100")) == Decimal("0.0200")


def test_calculate_atr_pct_missing_or_zero_close_is_none():
    assert calculate_atr_pct(None, Decimal("100")) is None
    assert calculate_atr_pct(Decimal("2"), Decimal("0")) is None


def test_calculate_extreme_hand_computed():
    values = _decimals(range(1, 25))
    assert calculate_extreme(values, lookback_days=10, pick_max=True) == Decimal("24")
    assert calculate_extreme(values, lookback_days=10, pick_max=False) == Decimal("15")


def test_calculate_extreme_empty_is_none():
    assert calculate_extreme([], lookback_days=10, pick_max=True) is None


def test_calculate_up_volume_ratio_hand_computed():
    closes = _decimals(
        [100, 101, 100, 102, 101, 103, 104, 103, 105, 106, 105, 107, 108, 107, 109, 110, 109, 111, 112, 111, 113]
    )
    volumes = list(range(1, 22))
    assert calculate_up_volume_ratio(closes, volumes, lookback_days=20) == Decimal("0.6609")


def test_calculate_up_volume_ratio_zero_volume_is_none():
    closes = _decimals([100] * 21)
    volumes = [0] * 21
    assert calculate_up_volume_ratio(closes, volumes, lookback_days=20) is None


def test_calculate_up_volume_ratio_insufficient_history_is_none():
    closes = _decimals([100, 101])
    volumes = [10, 20]
    assert calculate_up_volume_ratio(closes, volumes, lookback_days=20) is None


def test_calculate_return_pct_hand_computed():
    closes = _decimals(range(100, 122))  # 22 values: 100..121
    # (close_t - close_t-20) / close_t-20 = (121 - 101) / 101
    assert calculate_return_pct(closes, lookback_days=20) == Decimal("0.1980")


def test_calculate_return_pct_insufficient_history_is_none():
    closes = _decimals(range(1, 10))
    assert calculate_return_pct(closes, lookback_days=20) is None


def test_calculate_days_to_earnings_hand_computed():
    assert calculate_days_to_earnings(date(2026, 1, 1), date(2026, 1, 10)) == 9


def test_calculate_days_to_earnings_unknown_is_none():
    assert calculate_days_to_earnings(date(2026, 1, 1), None) is None


def _make_bars(n: int, *, start_price: int = 100, step: int = 1) -> list[PriceBar]:
    bars = []
    start_date = date(2026, 1, 1)
    for i in range(n):
        close = Decimal(start_price + step * i)
        bars.append(
            PriceBar(
                security_id=1,
                bar_date=start_date + timedelta(days=i),
                open=close - 1,
                high=close + 1,
                low=close - 2,
                close=close,
                volume=1000 + i,
            )
        )
    return bars


def _make_flat_bars(n: int, *, price: int = 100, volume: int = 0) -> list[PriceBar]:
    bars = []
    start_date = date(2026, 1, 1)
    for i in range(n):
        close = Decimal(price)
        bars.append(
            PriceBar(
                security_id=1,
                bar_date=start_date + timedelta(days=i),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=volume,
            )
        )
    return bars


def test_build_technical_facts_fewer_than_60_bars_flags_missing_facts():
    bars = _make_bars(20)
    facts = build_technical_facts(
        bars,
        next_earnings_date=None,
        as_of_date=bars[-1].bar_date,
    )
    facts_by_name = {fact.name: fact for fact in facts}
    assert facts_by_name["dist_sma50_pct"].value is None  # needs 50 bars
    assert facts_by_name["return_20d_pct"].value is None  # needs 21 bars


def test_build_technical_facts_flat_series_has_zero_atr():
    bars = _make_flat_bars(20, price=100, volume=0)
    facts = build_technical_facts(
        bars,
        next_earnings_date=None,
        as_of_date=bars[-1].bar_date,
    )
    facts_by_name = {fact.name: fact for fact in facts}
    assert facts_by_name["atr_14"].value == Decimal("0")
    assert facts_by_name["atr_14_pct"].value == Decimal("0.0000")


def test_build_technical_facts_zero_volume_has_no_volume_derived_ratios():
    bars = _make_flat_bars(25, price=100, volume=0)
    facts = build_technical_facts(
        bars,
        next_earnings_date=None,
        as_of_date=bars[-1].bar_date,
    )
    facts_by_name = {fact.name: fact for fact in facts}
    assert facts_by_name["up_volume_ratio_20d"].value is None


def test_build_technical_facts_metric_ids_use_as_of_date_and_are_unique():
    bars = _make_bars(70)
    as_of_date = bars[-1].bar_date
    facts = build_technical_facts(
        bars,
        next_earnings_date=date(2026, 3, 15),
        as_of_date=as_of_date,
    )
    metric_ids = [fact.metric_id for fact in facts]
    assert len(metric_ids) == len(set(metric_ids))
    assert all(metric_id.endswith(as_of_date.isoformat()) for metric_id in metric_ids)
