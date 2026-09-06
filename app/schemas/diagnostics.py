from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class UniverseDiagnosticsItem(BaseModel):
    ticker: str
    sector_tag: str
    price_bar_count: int
    latest_bar_date: Optional[date] = None
    price_age_days: Optional[int] = None
    has_technical_features: bool
    technical_as_of: Optional[date] = None
    has_fundamentals: bool
    fundamental_as_of: Optional[date] = None
    earnings_event_count: int
    next_earnings_date: Optional[date] = None
    document_chunk_count: int
    decision: str
    checklist_score: Optional[int] = None
    debug_reason: str


class UniverseCoverage(BaseModel):
    tickers_with_price_bars: int = 0
    tickers_with_200_bars: int = 0
    tickers_with_technical_features: int = 0
    tickers_with_fundamentals: int = 0
    tickers_with_earnings_events: int = 0
    tickers_with_document_chunks: int = 0
    latest_macro_daily_date: Optional[date] = None


class UniverseDiagnosticsResponse(BaseModel):
    generated_at: datetime
    engine_version: str
    universe_size: int
    coverage: UniverseCoverage
    blocked_by: dict[str, Any] = Field(default_factory=dict)
    items: list[UniverseDiagnosticsItem] = Field(default_factory=list)
