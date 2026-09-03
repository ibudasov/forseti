#!/usr/bin/env python
"""Generate the frozen fixture universe used by `make scorecard`.

This script is run once, by hand, whenever the fixture needs to change. Its
output — `tests/fixtures/scorecard/universe.json` — is committed, so the
scorecard entrypoint (`scripts/scorecard.py`) has no dependency on test code
or on this generator at run time.

The fixture deliberately spans outcomes: a `trade`, a `watchlist` produced by
a middling checklist score, a `watchlist` produced by stale price data
capping a would-be trade, a `watchlist` produced by an insufficient price
history, and two `no_trade` items (one vetoed, one scoring too low). Between
them they exercise every warning the deterministic engine can raise from
missing or stale data.

`SMA_LONG` in `app/services/analyzer.py` gates on 200 daily bars, so every
security except the deliberately-insufficient one carries at least 220 bars.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "scorecard" / "universe.json"

# Frozen "today" the scorecard entrypoint must pass to `run_screening`. Never
# derived from `date.today()` — that would make every run's numbers depend on
# the day it happened to run, which defeats the point of a scorecard.
FROZEN_TODAY = date(2026, 3, 1)


def build_uptrend_series(
    start_close: float,
    num_days: int,
    daily_gain: float,
    end_date: date,
    start_volume: int = 1_000_000,
) -> list[dict]:
    """Build `num_days` ascending daily price bars ending on `end_date`.

    Reused by ibudasov/forseti#28, which needs synthetic uptrend price series
    for its own fixtures — keep the series construction here rather than
    duplicating it.
    """
    start_date = end_date - timedelta(days=num_days - 1)
    bars = []
    for offset in range(num_days):
        bar_date = start_date + timedelta(days=offset)
        close = round(start_close + offset * daily_gain, 2)
        bars.append(
            {
                "bar_date": bar_date.isoformat(),
                "open": close,
                "high": round(close + 2.0, 2),
                "low": round(close - 2.0, 2),
                "close": close,
                "volume": start_volume + offset * 100,
            }
        )
    return bars


def build_trade_ready_series(start_close: float, end_date: date, start_volume: int = 1_000_000) -> list[dict]:
    """Build a 250-day series with a genuine swing high followed by a pullback.

    A strictly monotonic uptrend (`build_uptrend_series`) always has its
    60-bar swing high on the most recent bar, so `calculate_risk_levels`
    computes a take-profit that is only cents above the current close and
    every candidate trade fails `risk_reward_below_min`. This helper instead
    rises, peaks, and pulls back within the trailing 60 bars — the shape real
    swing-trade setups have — so the risk math actually produces a valid
    trade.
    """
    num_days = 250
    base_days, rise_days, pullback_days = 190, 40, 20
    base_gain, rise_gain, pullback_drop = 0.3, 1.0, 1.0

    start_date = end_date - timedelta(days=num_days - 1)
    closes = []
    close = start_close
    for _ in range(base_days):
        closes.append(close)
        close = round(close + base_gain, 2)
    for _ in range(rise_days):
        closes.append(close)
        close = round(close + rise_gain, 2)
    for _ in range(pullback_days):
        closes.append(close)
        close = round(close - pullback_drop, 2)

    bars = []
    for offset, day_close in enumerate(closes):
        bar_date = start_date + timedelta(days=offset)
        bars.append(
            {
                "bar_date": bar_date.isoformat(),
                "open": day_close,
                "high": round(day_close + 2.0, 2),
                "low": round(day_close - 2.0, 2),
                "close": day_close,
                "volume": start_volume + offset * 100,
            }
        )
    return bars


def _technical_feature(as_of: date, rsi_14: float, sma_50: float, sma_200: float, volume_trend: Optional[float]) -> dict:
    return {
        "as_of_date": as_of.isoformat(),
        "rsi_14": rsi_14,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "volume_trend": volume_trend,
    }


def _fundamental(
    as_of: date,
    revenue_growth: Optional[float],
    fcf: Optional[float],
    debt_to_equity: Optional[float],
    eps_trend: Optional[float],
    margins: Optional[float],
) -> dict:
    return {
        "as_of_date": as_of.isoformat(),
        "revenue_growth": revenue_growth,
        "fcf": fcf,
        "debt_to_equity": debt_to_equity,
        "eps_trend": eps_trend,
        "margins": margins,
    }


def build_universe() -> dict:
    fresh_end = FROZEN_TODAY - timedelta(days=1)
    stale_end = FROZEN_TODAY - timedelta(days=30)

    securities = [
        {
            # Clean trade: full fundamentals, healthy technicals, earnings
            # far enough out to dodge the earnings-too-close veto, fresh
            # bars. Zero warnings.
            "ticker": "TRENDCORE",
            "name": "Trendcore Robotics",
            "exchange": "NASDAQ",
            "sector_tag": "robotics",
            "price_bars": build_trade_ready_series(100.0, fresh_end),
            "technical_feature": _technical_feature(fresh_end, 55.0, 90.0, 80.0, 1.1),
            "fundamental": _fundamental(date(2025, 12, 31), 0.30, 1_000_000.0, 0.40, 0.10, 0.20),
            "earnings_event": {"report_date": (FROZEN_TODAY + timedelta(days=60)).isoformat(), "confirmed": False},
        },
        {
            # Watchlist via a middling checklist score (7/11): partial
            # fundamentals, no earnings event on file.
            "ticker": "VALUEWATCH",
            "name": "Valuewatch Defence",
            "exchange": "NYSE",
            "sector_tag": "defence",
            "price_bars": build_uptrend_series(80.0, 250, 0.04, fresh_end),
            "technical_feature": _technical_feature(fresh_end, 55.0, 70.0, 60.0, None),
            "fundamental": _fundamental(date(2025, 12, 31), 0.20, None, None, 0.05, None),
            "earnings_event": None,
        },
        {
            # Watchlist via a gate cap: checklist score is trade-worthy
            # (11/11) but the price history is 30 days stale, which
            # downgrades trade to watchlist regardless of score.
            "ticker": "STALESIGNAL",
            "name": "Stalesignal Quantum",
            "exchange": "NASDAQ",
            "sector_tag": "quantum",
            "price_bars": build_uptrend_series(100.0, 250, 0.05, stale_end),
            "technical_feature": _technical_feature(stale_end, 55.0, 90.0, 80.0, 1.1),
            "fundamental": _fundamental(date(2025, 12, 31), 0.30, 1_000_000.0, 0.40, 0.10, 0.20),
            "earnings_event": None,
        },
        {
            # Watchlist via the data gate itself: fewer than the 200 bars
            # `SMA_LONG` requires, so the gate returns `insufficient_price_data`
            # before any scoring happens.
            "ticker": "GATEWATCH",
            "name": "Gatewatch Space",
            "exchange": "NYSE",
            "sector_tag": "space",
            "price_bars": build_uptrend_series(50.0, 150, 0.03, fresh_end),
            "technical_feature": None,
            "fundamental": None,
            "earnings_event": None,
        },
        {
            # No_trade via a hard veto: RSI overbought at 75 vetoes the
            # trade outright regardless of checklist score. No fundamentals
            # on file either.
            "ticker": "RISKYCALL",
            "name": "Riskycall Nuclear",
            "exchange": "NYSE",
            "sector_tag": "nuclear",
            "price_bars": build_uptrend_series(100.0, 250, 0.05, fresh_end),
            "technical_feature": _technical_feature(fresh_end, 75.0, 110.0, 120.0, 0.8),
            "fundamental": None,
            "earnings_event": None,
        },
        {
            # No_trade via a checklist score too low to reach watchlist:
            # no fundamentals, no technical features on file at all.
            "ticker": "STEADYHOLD",
            "name": "Steadyhold Green Energy",
            "exchange": "NASDAQ",
            "sector_tag": "green_energy",
            "price_bars": build_uptrend_series(60.0, 250, 0.02, fresh_end),
            "technical_feature": None,
            "fundamental": None,
            "earnings_event": None,
        },
    ]

    return {
        "today": FROZEN_TODAY.isoformat(),
        "macro_daily": {"obs_date": FROZEN_TODAY.isoformat(), "vix": 15.0},
        "securities": securities,
    }


def main() -> None:
    universe = build_universe()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(universe, sort_keys=True, indent=2) + "\n")
    print(f"Wrote {FIXTURE_PATH}")


if __name__ == "__main__":
    main()
