from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.fundamentals import DeterministicFundamentalAnalysis

SCHEMA_VERSION: Literal["1.0"] = "1.0"
PRIMARY_QUALITY_TIER: Literal["primary"] = "primary"
SECONDARY_QUALITY_TIER: Literal["secondary"] = "secondary"
TERTIARY_QUALITY_TIER: Literal["tertiary"] = "tertiary"


class FundamentalObservationPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str
    annual: list[FundamentalObservationPoint]
    quarterly: list[FundamentalObservationPoint]


class FundamentalHistorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ticker: str
    series: list[FundamentalHistorySeries]


class MetricSeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str
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


class MetricSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str
    annual: list[MetricSeriesPoint] = Field(default_factory=list)
    quarterly: list[MetricSeriesPoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_periods(self) -> "MetricSeries":
        _assert_unique_metric_ids(self.annual + self.quarterly)
        _assert_sorted_points(self.annual, expected_periods={"FY"})
        _assert_sorted_points(self.quarterly, expected_periods={"Q1", "Q2", "Q3", "Q4"})
        return self


class EvidenceChunkInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: int
    retrieval_question_id: str
    source_type: str
    source_url: str
    source_hash: str
    chunk_index: int
    published_at: Optional[datetime] = None
    quality_tier: Literal["primary", "secondary", "tertiary"]
    text: str


class EvidenceQuestionCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    chunk_ids: list[int] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    truncated: bool = False


class FundamentalContextCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_metrics_present: list[str] = Field(default_factory=list)
    required_metrics_missing: list[str] = Field(default_factory=list)
    annual_period_counts: dict[str, int] = Field(default_factory=dict)
    quarterly_period_counts: dict[str, int] = Field(default_factory=dict)
    source_types_present: list[str] = Field(default_factory=list)
    source_types_missing: list[str] = Field(default_factory=list)
    newest_evidence_published_at: Optional[datetime] = None
    evidence_stale: bool = False
    evidence_truncated: bool = False
    metric_series_truncated: bool = False
    selected_chunk_count: int = 0
    selected_character_count: int = 0
    question_coverage: list[EvidenceQuestionCoverage] = Field(default_factory=list)


class FundamentalAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    context_hash: str
    ticker: str
    company_name: str
    sector: str
    currency: str
    snapshot_at: datetime
    as_of_date: date
    deterministic_result: DeterministicFundamentalAnalysis
    metric_series: list[MetricSeries] = Field(default_factory=list)
    evidence_chunks: list[EvidenceChunkInput] = Field(default_factory=list)
    coverage: FundamentalContextCoverage
    allowed_adjustment_min: int = -2
    allowed_adjustment_max: int = 2

    @model_validator(mode="after")
    def _validate_request(self) -> "FundamentalAnalysisRequest":
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not self.context_hash.strip():
            raise ValueError("context_hash must not be empty")
        if self.allowed_adjustment_min > 0 or self.allowed_adjustment_max < 0:
            raise ValueError("Allowed adjustment range must include zero.")
        if self.allowed_adjustment_min > self.allowed_adjustment_max:
            raise ValueError("Allowed adjustment minimum must be less than or equal to the maximum.")
        _assert_unique_chunk_ids(self.evidence_chunks)
        _assert_unique_metric_series_ids(self.metric_series)
        return self


class CitedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    category: Literal[
        "growth_quality",
        "cash_flow_quality",
        "balance_sheet",
        "profitability",
        "competitive_position",
        "management_guidance",
        "concentration",
        "accounting_quality",
        "other",
    ]
    direction: Literal["positive", "negative", "mixed"]
    materiality: Literal["low", "medium", "high"]
    claim: str
    metric_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_claim(self) -> "CitedFinding":
        if not self.claim.strip():
            raise ValueError("Finding claim must not be empty.")
        if self.materiality in {"medium", "high"} and not (self.metric_ids or self.chunk_ids):
            raise ValueError("Medium/high-materiality findings must include at least one citation.")
        return self


class FundamentalAssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    context_hash: str
    agent_name: Literal["fundamental_analyst"] = "fundamental_analyst"
    status: Literal["completed", "insufficient_data", "failed"]
    overall_signal: Literal[
        "strong_negative",
        "negative",
        "neutral",
        "positive",
        "strong_positive",
    ]
    proposed_score_adjustment: int
    findings: list[CitedFinding] = Field(default_factory=list)
    contradictions: list[CitedFinding] = Field(default_factory=list)
    material_red_flags: list[CitedFinding] = Field(default_factory=list)
    evidence_coverage: float = Field(ge=0, le=1)
    missing_information: list[str] = Field(default_factory=list)
    summary: str

    @model_validator(mode="after")
    def _validate_response(self) -> "FundamentalAssessmentResponse":
        if self.status in {"insufficient_data", "failed"} and self.proposed_score_adjustment != 0:
            raise ValueError("Insufficient-data and failed responses must propose adjustment 0.")
        if not self.summary.strip():
            raise ValueError("Assessment summary must not be empty.")
        _assert_unique_finding_ids(self.findings, self.contradictions, self.material_red_flags)
        return self


class FundamentalAssessmentValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    reason_codes: list[str] = Field(default_factory=list)


class FundamentalAssessmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response: FundamentalAssessmentResponse
    validation: FundamentalAssessmentValidation
    raw_output: str = ""
    latency_ms: float = Field(ge=0, default=0.0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    model_name: str
    prompt_version: str


class FundamentalAgentEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "shadow", "enforced"]
    accepted: bool
    reason_codes: list[str] = Field(default_factory=list)
    raw_adjustment: int
    applied_adjustment: int
    baseline_fundamental_score: int = Field(ge=0)
    adjusted_fundamental_score: int = Field(ge=0)
    baseline_total_score: int = Field(ge=0)
    adjusted_total_score: int = Field(ge=0)
    baseline_decision: Literal["trade", "watchlist", "no_trade"]
    counterfactual_decision: Literal["trade", "watchlist", "no_trade"]
    final_decision: Literal["trade", "watchlist", "no_trade"]
    decision_changed: bool


def _assert_unique_metric_ids(points: list[MetricSeriesPoint]) -> None:
    metric_ids = [point.metric_id for point in points]
    if len(metric_ids) != len(set(metric_ids)):
        raise ValueError("Metric series point IDs must be unique.")


def _assert_sorted_points(
    points: list[MetricSeriesPoint],
    *,
    expected_periods: set[str],
) -> None:
    if any(point.fiscal_period not in expected_periods for point in points):
        raise ValueError("Metric series contains points in an unexpected fiscal period bucket.")
    ordered = sorted(
        points,
        key=lambda point: (
            point.period_end,
            point.filed_at or date.min,
            point.accession_number or "",
            point.metric_id,
        ),
    )
    if points != ordered:
        raise ValueError("Metric series points must be ordered deterministically.")


def _assert_unique_chunk_ids(chunks: list[EvidenceChunkInput]) -> None:
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("Evidence chunk IDs must be unique.")


def _assert_unique_metric_series_ids(series_list: list[MetricSeries]) -> None:
    metric_ids = [
        point.metric_id
        for series in series_list
        for point in [*series.annual, *series.quarterly]
    ]
    if len(metric_ids) != len(set(metric_ids)):
        raise ValueError("Metric series point IDs must be unique across the request.")


def _assert_unique_finding_ids(*groups: list[CitedFinding]) -> None:
    finding_ids = [finding.finding_id for group in groups for finding in group]
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("Finding IDs must be unique across the full assessment response.")
