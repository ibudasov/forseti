from __future__ import annotations

from datetime import date, datetime
from importlib import import_module
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.critique import AnalystView
from app.schemas.memo import DecisionMemoItem

if TYPE_CHECKING:
    from app.schemas.analyze import DecisionDiagnosis

SYNTHESIS_SCHEMA_VERSION: Literal["1.0"] = "1.0"
SYNTHESIS_DECISION = Literal["trade", "watchlist", "no_trade"]
_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}


class DecisionSynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SYNTHESIS_SCHEMA_VERSION
    run_id: str
    context_hash: str
    ticker: str
    as_of_date: date
    snapshot_at: datetime
    baseline_decision: SYNTHESIS_DECISION
    baseline_confidence: float = Field(ge=0, le=1)
    entry_range: tuple[float, float] | None = None
    stop_loss: float | None = None
    take_profit: tuple[float, float] | None = None
    risk_reward: float | None = None
    position_size_eur: float | None = None
    time_stop_at: date | None = None
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnosis: "DecisionDiagnosis | None" = None
    analyst_views: list[AnalystView] = Field(default_factory=list)
    known_metric_ids: list[str] = Field(default_factory=list)
    known_chunk_ids: list[int] = Field(default_factory=list)
    known_warning_codes: list[str] = Field(default_factory=list)
    critic_findings: list[str] = Field(default_factory=list)
    allowed_decisions: list[SYNTHESIS_DECISION] = Field(default_factory=list)
    max_confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _validate_authority_and_ids(self) -> "DecisionSynthesisRequest":
        if not self.run_id.strip() or not self.context_hash.strip():
            raise ValueError("run_id and context_hash must not be empty.")
        if self.max_confidence != self.baseline_confidence:
            raise ValueError("max_confidence must equal baseline_confidence.")
        if not self.allowed_decisions or self.baseline_decision not in self.allowed_decisions:
            raise ValueError("allowed_decisions must include the baseline decision.")
        if any(_DECISION_RANK[decision] > _DECISION_RANK[self.baseline_decision] for decision in self.allowed_decisions):
            raise ValueError("allowed_decisions must not include promotions.")
        _assert_unique(self.known_metric_ids, "Metric IDs")
        _assert_unique(self.known_chunk_ids, "Chunk IDs")
        _assert_unique(self.known_warning_codes, "Warning codes")
        _assert_unique(self.allowed_decisions, "Allowed decisions")
        return self


class DecisionMemoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SYNTHESIS_SCHEMA_VERSION
    run_id: str
    context_hash: str
    proposed_decision: SYNTHESIS_DECISION
    confidence: float = Field(ge=0, le=1)
    thesis: str
    key_reasons: list[DecisionMemoItem] = Field(default_factory=list)
    key_risks: list[DecisionMemoItem] = Field(default_factory=list)
    what_would_change: list[DecisionMemoItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_memo(self) -> "DecisionMemoResponse":
        if not self.thesis.strip():
            raise ValueError("Memo thesis must not be empty.")
        return self


class DecisionMemoValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    reason_codes: list[str] = Field(default_factory=list)


class DecisionMemoResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response: DecisionMemoResponse
    validation: DecisionMemoValidation
    rendered_memo: str
    used_fallback: bool
    raw_output: str = ""
    latency_ms: float = Field(ge=0, default=0.0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    model_name: str
    prompt_version: str


def _assert_unique(values: list, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique.")


_DecisionDiagnosis = getattr(import_module("app.schemas.analyze"), "DecisionDiagnosis")
DecisionSynthesisRequest.model_rebuild(_types_namespace={"DecisionDiagnosis": _DecisionDiagnosis})
