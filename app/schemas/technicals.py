from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.technicals import DeterministicTechnicalAnalysis

SCHEMA_VERSION: Literal["1.0"] = "1.0"

TechnicalFactUnit = Literal["price", "pct", "ratio", "rsi_points", "index_points", "days", "bool"]


class TechnicalFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str
    name: str
    value: Optional[Decimal] = None
    unit: TechnicalFactUnit
    derivation: str

    @model_validator(mode="after")
    def _validate_fact(self) -> "TechnicalFact":
        if not self.metric_id.strip():
            raise ValueError("metric_id must not be empty")
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if not self.derivation.strip():
            raise ValueError("derivation must not be empty")
        return self


class IndicatorSeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bar_date: date
    close: Decimal
    sma_50: Optional[Decimal] = None
    sma_200: Optional[Decimal] = None
    rsi_14: Optional[Decimal] = None
    volume: int


class TechnicalContextCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bar_count: int = Field(ge=0)
    required_facts_missing: list[str] = Field(default_factory=list)
    latest_bar_date: Optional[date] = None
    bars_stale: bool = False
    series_truncated: bool = False


class TechnicalAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    context_hash: str
    ticker: str
    sector: str
    snapshot_at: datetime
    as_of_date: date
    deterministic_result: DeterministicTechnicalAnalysis
    facts: list[TechnicalFact] = Field(default_factory=list)
    series: list[IndicatorSeriesPoint] = Field(default_factory=list)
    coverage: TechnicalContextCoverage
    allowed_adjustment_min: int = -2
    allowed_adjustment_max: int = 0

    @model_validator(mode="after")
    def _validate_request(self) -> "TechnicalAnalysisRequest":
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not self.context_hash.strip():
            raise ValueError("context_hash must not be empty")
        if self.allowed_adjustment_max != 0:
            raise ValueError("Technical adjustments are downgrade-only: allowed_adjustment_max must be 0.")
        if self.allowed_adjustment_min > self.allowed_adjustment_max:
            raise ValueError("Allowed adjustment minimum must be less than or equal to the maximum.")
        _assert_unique_metric_ids(self.facts)
        _assert_ascending_series(self.series)
        return self


def _assert_unique_metric_ids(facts: list[TechnicalFact]) -> None:
    metric_ids = [fact.metric_id for fact in facts]
    if len(metric_ids) != len(set(metric_ids)):
        raise ValueError("Technical fact metric IDs must be unique.")


def _assert_ascending_series(series: list[IndicatorSeriesPoint]) -> None:
    bar_dates = [point.bar_date for point in series]
    if bar_dates != sorted(bar_dates):
        raise ValueError("Indicator series points must be ordered by ascending bar_date.")
