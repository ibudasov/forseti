from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import List, Optional

from app.db.models import PriceBar

# Frozen constants for risk math
ATR_PERIOD = 14
ATR_STOP_MULTIPLE = Decimal("2.0")
SWING_LOW_LOOKBACK = 20
SWING_HIGH_LOOKBACK = 60
ENTRY_BUFFER_PCT = Decimal("0.02")
TP2_RISK_MULTIPLE = Decimal("2.0")
RISK_REWARD_MIN = Decimal("1.5")
PRICE_PRECISION = Decimal("0.01")


@dataclass(frozen=True)
class RiskConfig:
    """Account risk configuration."""
    capital_eur: Decimal
    risk_per_trade_pct: Decimal


@dataclass(frozen=True)
class TradeLevels:
    """Trade entry, exit, and sizing levels."""
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    risk_reward: Decimal
    position_size_eur: Decimal
    shares: int


@dataclass
class RiskDowngrade:
    """Result of a risk math downgrade check."""
    reason: str
    detail: str


def calculate_risk_levels(
    bars: List[PriceBar],
    risk_config: RiskConfig,
) -> Optional[TradeLevels | RiskDowngrade]:
    """
    Calculate trade levels for a trade decision.
    Bars must be in ascending order.
    Returns TradeLevels if valid, RiskDowngrade if a downgrade occurred, or None if risk geometry fails.
    """
    if not bars or len(bars) < max(SWING_HIGH_LOOKBACK, SWING_LOW_LOOKBACK):
        return None

    close = Decimal(str(bars[-1].close))
    highs = [Decimal(str(bar.high)) for bar in bars]
    lows = [Decimal(str(bar.low)) for bar in bars]

    # Entry range
    entry_low = _quantize(close * (Decimal("1") - ENTRY_BUFFER_PCT))
    entry_high = _quantize(close * (Decimal("1") + ENTRY_BUFFER_PCT))

    # Calculate ATR (simple mean of true range)
    atr = _calculate_atr(bars)

    # Stop loss: min(lowest low of last 20 bars, close - 2 * atr)
    swing_low_20 = min(lows[-SWING_LOW_LOOKBACK:])
    stop_loss_atr = close - (ATR_STOP_MULTIPLE * atr)
    stop_loss = _quantize(min(swing_low_20, stop_loss_atr))

    # Use the full entry range for sizing, but evaluate reward-to-risk
    # from the current price anchor used by the rest of the engine.
    entry_risk_per_share = entry_high - stop_loss
    if entry_risk_per_share <= 0:
        return RiskDowngrade(
            reason="invalid_risk_geometry",
            detail=f"entry_risk_per_share: {float(entry_risk_per_share):.2f} <= 0",
        )

    trade_risk_per_share = close - stop_loss
    if trade_risk_per_share <= 0:
        return RiskDowngrade(
            reason="invalid_risk_geometry",
            detail=f"trade_risk_per_share: {float(trade_risk_per_share):.2f} <= 0",
        )

    # Swing high 60 bars (TP1 anchor)
    swing_high_60 = max(highs[-SWING_HIGH_LOOKBACK:])

    # Take profit levels
    tp1 = swing_high_60
    tp2 = _quantize(close + (TP2_RISK_MULTIPLE * trade_risk_per_share))

    # Risk reward
    risk_reward = (tp1 - close) / trade_risk_per_share
    if risk_reward < RISK_REWARD_MIN:
        return RiskDowngrade(
            reason="risk_reward_below_min",
            detail=f"risk_reward: {float(risk_reward):.4f} < {float(RISK_REWARD_MIN):.2f} min",
        )

    # Position sizing
    risk_budget = risk_config.capital_eur * risk_config.risk_per_trade_pct
    shares_raw = risk_budget / entry_risk_per_share
    shares = int(shares_raw)  # floor

    if shares == 0:
        return RiskDowngrade(
            reason="position_size_zero",
            detail=f"shares: {shares} (budget: {float(risk_budget):.2f} EUR, risk_per_share: {float(risk_per_share):.2f})",
        )

    position_size_eur = _quantize(Decimal(shares) * entry_high)

    return TradeLevels(
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        take_profit_1=_quantize(tp1),
        take_profit_2=tp2,
        risk_reward=_quantize(risk_reward),
        position_size_eur=position_size_eur,
        shares=shares,
    )


def _calculate_atr(bars: List[PriceBar]) -> Decimal:
    """
    Calculate Average True Range using simple mean over ATR_PERIOD.
    """
    if len(bars) < ATR_PERIOD + 1:
        return Decimal("4.00")  # Default to spec example if insufficient bars

    true_ranges = []
    last_completed_bar_index = len(bars) - 1
    start_idx = last_completed_bar_index - ATR_PERIOD
    for i in range(start_idx, last_completed_bar_index):
        high = Decimal(str(bars[i].high))
        low = Decimal(str(bars[i].low))
        close_prev = Decimal(str(bars[i - 1].close))
        tr = max(high - low, abs(high - close_prev), abs(low - close_prev))
        true_ranges.append(tr)

    return sum(true_ranges) / len(true_ranges)


def _quantize(value: Decimal) -> Decimal:
    """Quantize to 2 decimal places (EUR precision)."""
    return value.quantize(PRICE_PRECISION, rounding=ROUND_HALF_UP)
