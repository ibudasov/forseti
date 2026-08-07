from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional, Tuple

from app.db.models import EarningsEvent, PriceBar, TechnicalFeature

# Frozen constants for vetoes
RSI_OVERBOUGHT_VETO = 70
VIX_PANIC_VETO = 30
EARNINGS_VETO_DAYS = 7
SMA200_DEEP_VETO_PCT = Decimal("0.10")


@dataclass
class Veto:
    """Result of a veto check."""
    rule_id: str
    detail: str


def check_vetoes(
    rsi: Optional[Decimal],
    latest_bar: Optional[PriceBar],
    vix_close: Optional[Decimal],
    next_earnings: Optional[EarningsEvent],
    sma_200: Optional[Decimal],
    today: Optional[date] = None,
) -> Optional[Veto]:
    """
    Evaluate vetoes in strict order.
    Returns first veto or None if all pass.
    Null feature values never trigger a veto (fail open).
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    # Veto 1: RSI overbought
    if rsi is not None and rsi > RSI_OVERBOUGHT_VETO:
        return Veto(
            rule_id="rsi_overbought",
            detail=f"rsi_14: {float(rsi):.2f} > {RSI_OVERBOUGHT_VETO} max",
        )

    # Veto 2: VIX panic regime
    if vix_close is not None and vix_close > VIX_PANIC_VETO:
        return Veto(
            rule_id="vix_panic_regime",
            detail=f"vix: {float(vix_close):.2f} > {VIX_PANIC_VETO} panic threshold",
        )

    # Veto 3: Earnings too close
    if next_earnings is not None:
        days_to_earnings = (next_earnings.report_date - today).days
        if 0 <= days_to_earnings <= EARNINGS_VETO_DAYS:
            return Veto(
                rule_id="earnings_too_close",
                detail=f"earnings in {days_to_earnings} days (within {EARNINGS_VETO_DAYS} day window)",
            )

    # Veto 4: Deep below SMA200
    if latest_bar is not None and sma_200 is not None:
        close = Decimal(str(latest_bar.close))
        threshold = sma_200 * (Decimal("1") - SMA200_DEEP_VETO_PCT)
        if close < threshold:
            return Veto(
                rule_id="deep_below_sma200",
                detail=f"close: {float(close):.2f} < {float(threshold):.2f} (0.90 * sma_200)",
            )

    return None
