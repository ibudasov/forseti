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


class TechnicalFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    category: Literal[
        "trend",
        "momentum",
        "overextension",
        "support_resistance",
        "volume_confirmation",
        "volatility",
        "event_risk",
        "data_quality",
        "other",
    ]
    direction: Literal["positive", "negative", "mixed"]
    materiality: Literal["low", "medium", "high"]
    claim: str
    metric_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_claim(self) -> "TechnicalFinding":
        if not self.claim.strip():
            raise ValueError("Finding claim must not be empty.")
        if self.materiality in {"medium", "high"} and not self.metric_ids:
            raise ValueError("Medium/high-materiality findings must cite at least one metric_id.")
        return self


class TechnicalAssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    context_hash: str
    agent_name: Literal["technical_analyst"] = "technical_analyst"
    status: Literal["completed", "insufficient_data", "failed"]
    overall_signal: Literal[
        "strong_negative",
        "negative",
        "neutral",
        "positive",
        "strong_positive",
    ]
    trend_state: Literal["uptrend", "downtrend", "range", "transition", "unknown"]
    momentum_state: Literal["strengthening", "stable", "weakening", "overextended", "unknown"]
    proposed_score_adjustment: int
    findings: list[TechnicalFinding] = Field(default_factory=list)
    contradictions: list[TechnicalFinding] = Field(default_factory=list)
    risk_flags: list[TechnicalFinding] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    summary: str

    @model_validator(mode="after")
    def _validate_response(self) -> "TechnicalAssessmentResponse":
        if self.proposed_score_adjustment > 0:
            raise ValueError("Technical adjustments are downgrade-only.")
        if self.status in {"insufficient_data", "failed"} and self.proposed_score_adjustment != 0:
            raise ValueError("Insufficient-data and failed responses must propose adjustment 0.")
        if not self.summary.strip():
            raise ValueError("Assessment summary must not be empty.")
        _assert_unique_technical_finding_ids(self.findings, self.contradictions, self.risk_flags)
        return self


class TechnicalAssessmentValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    reason_codes: list[str] = Field(default_factory=list)


class TechnicalAssessmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response: TechnicalAssessmentResponse
    validation: TechnicalAssessmentValidation
    raw_output: str = ""
    latency_ms: float = Field(ge=0, default=0.0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    model_name: str
    prompt_version: str


def _assert_unique_technical_finding_ids(*groups: list[TechnicalFinding]) -> None:
    finding_ids = [finding.finding_id for group in groups for finding in group]
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("Finding IDs must be unique across the full assessment response.")


def _assert_unique_metric_ids(facts: list[TechnicalFact]) -> None:
    metric_ids = [fact.metric_id for fact in facts]
    if len(metric_ids) != len(set(metric_ids)):
        raise ValueError("Technical fact metric IDs must be unique.")


def _assert_ascending_series(series: list[IndicatorSeriesPoint]) -> None:
    bar_dates = [point.bar_date for point in series]
    if bar_dates != sorted(bar_dates):
        raise ValueError("Indicator series points must be ordered by ascending bar_date.")
