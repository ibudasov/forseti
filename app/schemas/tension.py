from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TensionConflictType = Literal["polarity_conflict", "signal_conflict", "technical_risk_flag"]
TensionSignal = Literal["strong_negative", "negative", "neutral", "positive", "strong_positive"]
TensionTrendState = Literal["uptrend", "downtrend", "range", "transition", "unknown"]
TensionMomentumState = Literal["strengthening", "stable", "weakening", "overextended", "unknown"]


class CrossDomainTension(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_type: TensionConflictType
    fundamental_signal: TensionSignal
    technical_signal: TensionSignal
    technical_trend_state: TensionTrendState
    technical_momentum_state: TensionMomentumState
    fundamental_metric_ids: list[str] = Field(default_factory=list)
    fundamental_chunk_ids: list[int] = Field(default_factory=list)
    technical_metric_ids: list[str] = Field(default_factory=list)
    technical_risk_flag_categories: list[str] = Field(default_factory=list)
    retrieval_query: str
    date_window_days: int = Field(gt=0)
    supplemental_chunk_ids: list[int] = Field(default_factory=list)
    summary: str

    @model_validator(mode="after")
    def _validate_non_empty_text(self) -> "CrossDomainTension":
        if not self.retrieval_query.strip():
            raise ValueError("retrieval_query must not be empty.")
        if not self.summary.strip():
            raise ValueError("summary must not be empty.")
        if len(self.supplemental_chunk_ids) != len(set(self.supplemental_chunk_ids)):
            raise ValueError("supplemental_chunk_ids must be unique.")
        return self
