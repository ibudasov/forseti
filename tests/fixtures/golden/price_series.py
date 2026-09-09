"""Deterministic synthetic price-series builders for golden cases."""
from __future__ import annotations

from datetime import date, timedelta


def build_trade_ready_series(
    *,
    bars: int = 250,
    start: float = 100.0,
    base_gain: float = 0.3,
    breakout_gain: float = 1.0,
    pullback_drop: float = 1.0,
    end_date: date,
    start_volume: int = 1_000_000,
) -> list[dict[str, object]]:
    """Build a rising series with a swing-high pullback the risk engine can trade."""
    if bars < 60:
        raise ValueError("trade-ready series requires at least 60 bars")

    breakout_bars = 40
    pullback_bars = 20
    base_bars = bars - breakout_bars - pullback_bars
    closes = []
    close = start
    for _ in range(base_bars):
        closes.append(round(close, 2))
        close += base_gain
    for _ in range(breakout_bars):
        closes.append(round(close, 2))
        close += breakout_gain
    for _ in range(pullback_bars):
        closes.append(round(close, 2))
        close -= pullback_drop
    return _bars_from_closes(closes, end_date=end_date, start_volume=start_volume)


def build_uptrend_series(
    *,
    bars: int = 250,
    start: float = 80.0,
    daily_gain: float = 0.08,
    intraday_range: float = 2.0,
    end_date: date,
    start_volume: int = 900_000,
) -> list[dict[str, object]]:
    """Build a smooth monotonic uptrend ending on `end_date`."""
    closes = [round(start + (index * daily_gain), 2) for index in range(bars)]
    return _bars_from_closes(
        closes,
        end_date=end_date,
        start_volume=start_volume,
        intraday_range=intraday_range,
    )


def _bars_from_closes(
    closes: list[float],
    *,
    end_date: date,
    start_volume: int,
    intraday_range: float = 2.0,
) -> list[dict[str, object]]:
    start_date = end_date - timedelta(days=len(closes) - 1)
    bars = []
    for offset, close in enumerate(closes):
        bar_date = start_date + timedelta(days=offset)
        bars.append(
            {
                "bar_date": bar_date.isoformat(),
                "open": close,
                "high": round(close + intraday_range, 2),
                "low": round(close - intraday_range, 2),
                "close": close,
                "volume": start_volume + (offset * 100),
            }
        )
    return bars
