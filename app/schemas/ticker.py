from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class PriceBarSnapshot(BaseModel):
    bar_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class TechnicalFeaturesSnapshot(BaseModel):
    as_of_date: date
    rsi_14: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    volume_trend: Optional[float] = None


class FundamentalsSnapshot(BaseModel):
    as_of_date: date
    revenue_growth: Optional[float] = None
    fcf: Optional[float] = None
    debt_to_equity: Optional[float] = None
    eps_trend: Optional[float] = None
    margins: Optional[float] = None


class DataFreshness(BaseModel):
    latest_price_bar_date: Optional[date] = None
    price_data_age_days: Optional[int] = None
    stale_threshold_days: int
    is_price_data_stale: bool


class TickerProfileResponse(BaseModel):
    ticker: str
    name: str
    exchange: str
    sector_tag: str
    currency: str
    is_active: bool
    latest_price_bar: Optional[PriceBarSnapshot] = None
    price_bars_stored: int
    data_freshness: DataFreshness
    latest_technical_features: Optional[TechnicalFeaturesSnapshot] = None
    latest_fundamentals: Optional[FundamentalsSnapshot] = None
    next_earnings_date: Optional[date] = None
    warnings: list[str]
