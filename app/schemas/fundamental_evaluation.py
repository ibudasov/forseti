from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.analyze import AnalyzeResponse
from app.schemas.fundamentals import FundamentalAnalysisRequest, FundamentalAssessmentResult

EVALUATION_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class AdjustmentRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: int = Field(ge=-2, le=2)
    maximum: int = Field(ge=-2, le=2)

    @model_validator(mode="after")
    def _validate_range(self) -> "AdjustmentRange":
        if self.minimum > self.maximum:
            raise ValueError("Adjustment range minimum must be less than or equal to maximum.")
        return self


class FundamentalEvaluationLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_status: Literal["completed", "insufficient_data", "failed"]
    acceptable_adjustment: AdjustmentRange
    acceptable_signals: list[
        Literal["strong_negative", "negative", "neutral", "positive", "strong_positive"]
    ] = Field(default_factory=list)
    expected_counterfactual_decision: Literal["trade", "watchlist", "no_trade"]
    appropriate_abstention: bool = False
    required_claim_keywords: list[str] = Field(default_factory=list)
    forbidden_claim_keywords: list[str] = Field(default_factory=list)
    required_red_flag_keywords: list[str] = Field(default_factory=list)


class FundamentalEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    category: str
    notes: str
    request: FundamentalAnalysisRequest
    deterministic_response: AnalyzeResponse
    recorded_result: FundamentalAssessmentResult
    label: FundamentalEvaluationLabel


class FundamentalEvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = EVALUATION_SCHEMA_VERSION
    generated_at: datetime
    cases: list[FundamentalEvaluationCase] = Field(default_factory=list)


class FundamentalEvaluationCaseReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    category: str
    baseline_decision: Literal["trade", "watchlist", "no_trade"]
    counterfactual_decision: Literal["trade", "watchlist", "no_trade"]
    status: Literal["completed", "insufficient_data", "failed"]
    accepted: bool
    agreement: bool
    adjustment: int
    outcome: Literal["promotion", "downgrade", "unchanged"]
    reason_codes: list[str] = Field(default_factory=list)


class FundamentalEvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_count: int
    structured_output_validity_rate: float
    citation_validity_rate: float
    material_claim_citation_coverage: float
    unsupported_claim_rate: float
    abstention_rate: float
    appropriate_abstention_precision: float
    agreement_rate: float
    duplicate_evidence_rejection_rate: float
    hard_blocker_violation_count: int
    risk_field_mutation_count: int
    provider_failure_rate: float
    neutral_fallback_rate: float
    adjustment_distribution: dict[str, int]
    outcome_counts: dict[str, int]
    status_counts: dict[str, int]
    counterfactual_confusion_matrix: dict[str, int]
    latency_ms: dict[str, float]
    token_usage: dict[str, float]


class FundamentalEvaluationGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    failures: list[str] = Field(default_factory=list)


class FundamentalReviewSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    category: str
    baseline_decision: Literal["trade", "watchlist", "no_trade"]
    counterfactual_decision: Literal["trade", "watchlist", "no_trade"]
    coverage: dict[str, object]
    proposed_adjustment: int
    status: Literal["completed", "insufficient_data", "failed"]
    findings: list[dict[str, object]] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)


class FundamentalEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metrics: FundamentalEvaluationMetrics
    gates: FundamentalEvaluationGateResult
    cases: list[FundamentalEvaluationCaseReport] = Field(default_factory=list)
    review_samples: list[FundamentalReviewSample] = Field(default_factory=list)


class FundamentalShadowReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_count: int
    version_breakdown: dict[str, int]
    outcome_counts: dict[str, int]
    status_counts: dict[str, int]
    rejection_reason_counts: dict[str, int]
    missing_information_counts: dict[str, int]
    baseline_counterfactual_matrix: dict[str, int]
    latency_ms: dict[str, float]
    token_usage: dict[str, float]
