from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


class ScreeningItem(BaseModel):
    ticker: str
    sector_tag: str
    status: Literal["ok", "error"]
    decision: Optional[Literal["trade", "watchlist", "no_trade"]] = None
    entry_range: Optional[Tuple[float, float]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[Tuple[float, float]] = None
    risk_reward: Optional[float] = None
    position_size_eur: Optional[float] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class ScreeningResponse(BaseModel):
    generated_at: datetime
    engine_version: str
    universe_size: int
    analyzed_count: int
    failed_count: int
    trade_count: int
    watchlist_count: int
    no_trade_count: int
    items: list[ScreeningItem]
    summary: Optional[dict[str, int]] = None

    model_config = ConfigDict(extra="allow")
