"""Risk Manager tool: calls the existing deterministic risk engine.

Wraps `app.services.risk.calculate_risk_levels`. Deterministic, no LLM
involved. This is the *only* source of entry range, stop-loss, take-profit,
position size, and risk/reward numbers in the agentic workflow.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from pydantic import BaseModel, Field

from app.db.repository import get_latest_bars
from app.services.risk import RiskConfig, RiskDowngrade, calculate_risk_levels

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class RiskManagerOutput(BaseModel):
    """Deterministic trade levels, or a downgrade reason when geometry is invalid."""

    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    risk_reward: Optional[float] = None
    position_size_eur: Optional[float] = None
    downgrade_reason: Optional[str] = Field(
        default=None, description="Set when risk geometry is invalid; the caller must downgrade the decision."
    )
    downgrade_detail: Optional[str] = None


CalculateRisk = Callable[[str], RiskManagerOutput]


def build_risk_manager_tool(
    risk_config: RiskConfig,
    engine: Optional["Engine"] = None,
) -> CalculateRisk:
    """Create a `calculate_risk` tool bound to the given collaborators."""

    def calculate_risk(ticker: str) -> RiskManagerOutput:
        """Calculate entry, stop, target, size, and risk/reward for a ticker.

        This is the single source of truth for trade numbers; LLM agents may
        only read this output, never recompute or override it.
        """
        bars = list(reversed(get_latest_bars(ticker, 250, engine=engine)))
        result = calculate_risk_levels(bars, risk_config)

        if result is None:
            return RiskManagerOutput()
        if isinstance(result, RiskDowngrade):
            return RiskManagerOutput(downgrade_reason=result.reason, downgrade_detail=result.detail)

        return RiskManagerOutput(
            entry_low=float(result.entry_low),
            entry_high=float(result.entry_high),
            stop_loss=float(result.stop_loss),
            take_profit_1=float(result.take_profit_1),
            take_profit_2=float(result.take_profit_2),
            risk_reward=float(result.risk_reward),
            position_size_eur=float(result.position_size_eur),
        )

    return calculate_risk
