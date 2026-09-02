#!/usr/bin/env python3
"""Generate the frozen fixture universe used by `scripts/scorecard.py`.

This script is run once, by hand, whenever the fixture needs to change; its
output (`tests/fixtures/scorecard/universe.json`) is committed and the
scorecard entrypoint reads that committed file. It never runs as part of
`make scorecard`, which keeps the scorecard free of any dependency on
generator/test code.

`build_uptrend_series` is deliberately generic (rally phase + optional
pullback phase) so that ibudasov/forseti#28 can reuse it for its own
synthetic price series instead of duplicating series construction.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "scorecard" / "universe.json"

# Every security ends its price history on FRESH_LAST_BAR_DATE, one day
# before the frozen "today" used by the scorecard, except STALE tickers,
# which deliberately end further back to trigger the `stale_price_data` gate.
TODAY = date(2026, 8, 23)
FRESH_LAST_BAR_DATE = TODAY - timedelta(days=1)
STALE_LAST_BAR_DATE = TODAY - timedelta(days=30)
PRICE_BAR_COUNT = 220


@dataclass
class Bar:
    bar_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def as_dict(self) -> dict:
        return {
            "bar_date": self.bar_date.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": self.volume,
        }


def build_uptrend_series(
    *,
    end_date: date,
    days: int,
    start_price: Decimal,
    daily_step: Decimal,
    volatility: Decimal = Decimal("0.10"),
    pullback_days: int = 0,
    pullback_step: Optional[Decimal] = None,
) -> List[Bar]:
    """Build `days` daily bars ending on `end_date`.

    The series rallies by `daily_step` per day for `days - pullback_days`
    bars, then (if `pullback_days` is set) retraces by `pullback_step` per
    day for the remaining bars. A rally-then-pullback shape is what gives the
    risk engine (swing high/low over the last 20/60 bars) a realistic,
    non-degenerate reward instead of collapsing to ~0 for a monotonic series.
    """
    rally_days = days - pullback_days
    start_date = end_date - timedelta(days=days - 1)

    bars: List[Bar] = []
    price = start_price
    current_date = start_date
    for _ in range(rally_days):
        bars.append(_build_bar(current_date, price, daily_step, volatility))
        price = bars[-1].close
        current_date += timedelta(days=1)

    step = pullback_step if pullback_step is not None else daily_step
    for _ in range(pullback_days):
        bars.append(_build_bar(current_date, price, -step, volatility))
        price = bars[-1].close
        current_date += timedelta(days=1)

    return bars


def _pin_reference_close(bars: List[Bar], reference_close: Decimal, volatility: Decimal = Decimal("0.10")) -> None:
    """Pin the oldest bar's close to `reference_close`, in place.

    `analyzer.analyze` reads its checklist/veto "current price" from the
    *oldest* bar of the fetched window rather than the newest one (a known,
    out-of-scope quirk of the existing engine -- see the analyzer's
    `latest_bar = bars[0]` line). Risk sizing, in contrast, anchors on the
    newest bar. Pinning only the oldest bar lets each fixture security carry
    one deliberate "current price" for checklist/veto comparisons while
    keeping the tail of the series free to shape realistic risk geometry.
    """
    first = bars[0]
    bars[0] = Bar(
        bar_date=first.bar_date,
        open=reference_close,
        high=reference_close + volatility,
        low=reference_close - volatility,
        close=reference_close,
        volume=first.volume,
    )


def _build_bar(bar_date: date, open_price: Decimal, step: Decimal, volatility: Decimal) -> Bar:
    close_price = open_price + step
    high_price = max(open_price, close_price) + volatility
    low_price = min(open_price, close_price) - volatility
    return Bar(
        bar_date=bar_date,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=1_000_000,
    )


def _security(
    ticker: str,
    name: str,
    sector_tag: str,
    bars: List[Bar],
    *,
    reference_close: Decimal,
    technical_feature: Optional[dict] = None,
    fundamental: Optional[dict] = None,
    earnings_report_date: Optional[date] = None,
) -> dict:
    _pin_reference_close(bars, reference_close)
    as_of_date = bars[-1].bar_date
    return {
        "ticker": ticker,
        "name": name,
        "exchange": "NASDAQ",
        "sector_tag": sector_tag,
        "price_bars": [bar.as_dict() for bar in bars],
        "technical_feature": (
            {"as_of_date": as_of_date.isoformat(), **technical_feature}
            if technical_feature is not None
            else None
        ),
        "fundamental": (
            {"as_of_date": as_of_date.isoformat(), **fundamental}
            if fundamental is not None
            else None
        ),
        "earnings_event": (
            {"report_date": earnings_report_date.isoformat(), "confirmed": True}
            if earnings_report_date is not None
            else None
        ),
    }


def generate_universe() -> dict:
    # ALPHA1: strong fundamentals + technicals + valid risk geometry -> trade.
    alpha_bars = build_uptrend_series(
        end_date=FRESH_LAST_BAR_DATE,
        days=PRICE_BAR_COUNT,
        start_price=Decimal("100.00"),
        daily_step=Decimal("0.50"),
        pullback_days=60,
        pullback_step=Decimal("0.15"),
    )
    alpha = _security(
        "ALPHA1",
        "Alpha One Robotics",
        "ai",
        alpha_bars,
        reference_close=Decimal("150.00"),
        technical_feature={
            "rsi_14": "55.0000",
            "sma_50": "140.0000",
            "sma_200": "110.0000",
            "volume_trend": "1.200000",
        },
        fundamental={
            "revenue_growth": "0.200000",
            "fcf": "1000000.0000",
            "debt_to_equity": "0.500000",
            "eps_trend": "0.100000",
            "margins": "0.180000",
            "raw_payload": {"source": "scorecard_fixture"},
        },
        earnings_report_date=TODAY + timedelta(days=90),
    )

    # BETA1: mid score (no vetoes, no gate warnings) -> watchlist.
    beta_bars = build_uptrend_series(
        end_date=FRESH_LAST_BAR_DATE,
        days=PRICE_BAR_COUNT,
        start_price=Decimal("100.00"),
        daily_step=Decimal("-0.05"),
    )
    beta = _security(
        "BETA1",
        "Beta One Defence",
        "defence",
        beta_bars,
        reference_close=Decimal("90.00"),
        technical_feature={
            "rsi_14": "55.0000",
            "sma_50": "95.0000",
            "sma_200": "80.0000",
            "volume_trend": "0.800000",
        },
        fundamental={
            "revenue_growth": "0.050000",
            "fcf": "200.0000",
            "debt_to_equity": "0.900000",
            "eps_trend": "-0.020000",
            "margins": "0.050000",
            "raw_payload": {"source": "scorecard_fixture"},
        },
        earnings_report_date=TODAY + timedelta(days=90),
    )

    # GAMMA1: RSI overbought veto -> no_trade.
    gamma_bars = build_uptrend_series(
        end_date=FRESH_LAST_BAR_DATE,
        days=PRICE_BAR_COUNT,
        start_price=Decimal("100.00"),
        daily_step=Decimal("0.05"),
    )
    gamma = _security(
        "GAMMA1",
        "Gamma One Nuclear",
        "nuclear",
        gamma_bars,
        reference_close=Decimal("100.00"),
        technical_feature={
            "rsi_14": "75.0000",
            "sma_50": "95.0000",
            "sma_200": "90.0000",
            "volume_trend": "1.100000",
        },
        fundamental={
            "revenue_growth": "0.180000",
            "fcf": "500.0000",
            "debt_to_equity": "0.400000",
            "eps_trend": "0.050000",
            "margins": "0.150000",
            "raw_payload": {"source": "scorecard_fixture"},
        },
    )

    # DELTA1: low checklist score, no veto -> no_trade.
    delta_bars = build_uptrend_series(
        end_date=FRESH_LAST_BAR_DATE,
        days=PRICE_BAR_COUNT,
        start_price=Decimal("100.00"),
        daily_step=Decimal("-0.05"),
    )
    delta = _security(
        "DELTA1",
        "Delta One Green Energy",
        "green_energy",
        delta_bars,
        reference_close=Decimal("90.00"),
        technical_feature={
            "rsi_14": "40.0000",
            "sma_50": "95.0000",
            "sma_200": "95.0000",
            "volume_trend": "0.500000",
        },
        fundamental={
            "revenue_growth": "0.050000",
            "fcf": "-50.0000",
            "debt_to_equity": "1.500000",
            "eps_trend": "-0.100000",
            "margins": "0.020000",
            "raw_payload": {"source": "scorecard_fixture"},
        },
    )

    # EPSILON1: missing fundamentals -> `no_fundamentals` data-gate warning, watchlist.
    epsilon_bars = build_uptrend_series(
        end_date=FRESH_LAST_BAR_DATE,
        days=PRICE_BAR_COUNT,
        start_price=Decimal("100.00"),
        daily_step=Decimal("0.00"),
    )
    epsilon = _security(
        "EPSILON1",
        "Epsilon One Quantum",
        "quantum",
        epsilon_bars,
        reference_close=Decimal("100.00"),
        technical_feature={
            "rsi_14": "55.0000",
            "sma_50": "95.0000",
            "sma_200": "80.0000",
            "volume_trend": "1.200000",
        },
        fundamental=None,
    )

    # ZETA1: strong score but stale prices -> `stale_price_data` gate cap, watchlist.
    zeta_bars = build_uptrend_series(
        end_date=STALE_LAST_BAR_DATE,
        days=PRICE_BAR_COUNT,
        start_price=Decimal("100.00"),
        daily_step=Decimal("0.50"),
        pullback_days=60,
        pullback_step=Decimal("0.15"),
    )
    zeta = _security(
        "ZETA1",
        "Zeta One Space",
        "space",
        zeta_bars,
        reference_close=Decimal("150.00"),
        technical_feature={
            "rsi_14": "55.0000",
            "sma_50": "140.0000",
            "sma_200": "110.0000",
            "volume_trend": "1.200000",
        },
        fundamental={
            "revenue_growth": "0.200000",
            "fcf": "1000000.0000",
            "debt_to_equity": "0.500000",
            "eps_trend": "0.100000",
            "margins": "0.180000",
            "raw_payload": {"source": "scorecard_fixture"},
        },
        earnings_report_date=TODAY + timedelta(days=90),
    )

    return {
        "today": TODAY.isoformat(),
        "macro_daily": [{"obs_date": FRESH_LAST_BAR_DATE.isoformat(), "vix": "15.000"}],
        "securities": [alpha, beta, gamma, delta, epsilon, zeta],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    universe = generate_universe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(universe, indent=2, sort_keys=True) + "\n")
    print(f"Wrote fixture universe to {args.output}")


if __name__ == "__main__":
    main()
