from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class FundamentalObservationPoint(BaseModel):
    metric_name: str
    value: Decimal
    unit: Optional[str] = None
    period_start: date
    period_end: date
    fiscal_year: Optional[int] = None
    fiscal_period: str
    form_type: str
    filed_at: Optional[date] = None
    accession_number: Optional[str] = None
    source_concept: str
    source_url: str
    is_derived: bool
    derivation: Optional[str] = None


class FundamentalHistorySeries(BaseModel):
    metric_name: str
    annual: list[FundamentalObservationPoint]
    quarterly: list[FundamentalObservationPoint]


class FundamentalHistorySnapshot(BaseModel):
    ticker: str
    series: list[FundamentalHistorySeries]
