from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from app.services.analyzer import validate_and_normalize_ticker


class AnalyzeRequest(BaseModel):
    ticker: str
    account_size_eur: Optional[float] = Field(default=None, gt=0)
    risk_percentage: Optional[float] = Field(default=None, gt=0, le=1)
    max_position_size_eur: Optional[float] = Field(default=None, gt=0)
    as_of_date: Optional[date] = None
    notes: Optional[str] = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return validate_and_normalize_ticker(value)


class AnalyzeResponse(BaseModel):
    ticker: str
    decision: Literal["trade", "watchlist", "no_trade"]
    entry_range: Optional[Tuple[float, float]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[Tuple[float, float]] = None
    risk_reward: Optional[float] = None
    position_size_eur: Optional[float] = None
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
    warnings: list[str]
    engine_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    trace_id: str

    class Config:
        # Allow mutation for trace_id
        populate_by_name = True
