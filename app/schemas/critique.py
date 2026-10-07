from __future__ import annotations

from importlib import import_module
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.fundamentals import CitedFinding, EvidenceChunkInput, SCHEMA_VERSION

if TYPE_CHECKING:
    from app.schemas.analyze import DecisionDiagnosis


class DraftRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["trade", "watchlist", "no_trade"]
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    entry_range: Optional[tuple[float, float]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[tuple[float, float]] = None
    risk_reward: Optional[float] = None
    position_size_eur: Optional[float] = None
    memo: str = ""
    source: Literal["deterministic", "decision_synthesizer"]

    @model_validator(mode="after")
    def _validate_trade_fields(self) -> "DraftRecommendation":
        if self.decision != "trade":
            return self
        required_trade_fields = {
            "entry_range": self.entry_range,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": self.risk_reward,
            "position_size_eur": self.position_size_eur,
        }
        missing_fields = [name for name, value in required_trade_fields.items() if value is None]
        if missing_fields:
            raise ValueError(
                "Trade recommendations must include "
                + ", ".join(missing_fields)
                + "."
            )
        return self


class AnalystView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: Literal["fundamental_analyst", "technical_analyst"]
    status: Literal["completed", "insufficient_data", "failed", "absent"]
    overall_signal: Literal[
        "strong_negative",
        "negative",
        "neutral",
        "positive",
        "strong_positive",
    ]
    findings: list[CitedFinding] = Field(default_factory=list)
    summary: str = ""

    @model_validator(mode="after")
    def _validate_absent_view(self) -> "AnalystView":
        if self.status != "absent":
            return self
        if self.overall_signal != "neutral":
            raise ValueError("Absent analyst views must use the neutral overall signal.")
        if self.findings:
            raise ValueError("Absent analyst views must not include findings.")
        if self.summary:
            raise ValueError("Absent analyst views must not include a summary.")
        return self


class CritiqueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    context_hash: str
    ticker: str
    as_of_date: date
    snapshot_at: datetime
    draft: DraftRecommendation
    analyst_views: list[AnalystView] = Field(default_factory=list)
    evidence_chunks: list[EvidenceChunkInput] = Field(default_factory=list)
    deterministic_warnings: list[str] = Field(default_factory=list)
    deterministic_diagnosis: Optional["DecisionDiagnosis"] = None
    allowed_actions: list[
        Literal[
            "accept",
            "downgrade_confidence",
            "downgrade_decision",
            "force_no_trade",
            "request_revision",
        ]
    ] = Field(default_factory=list)
    max_confidence_penalty: float = Field(default=0.30, ge=0, le=1)

    @model_validator(mode="after")
    def _validate_request(self) -> "CritiqueRequest":
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not self.context_hash.strip():
            raise ValueError("context_hash must not be empty")
        _assert_unique_chunk_ids(self.evidence_chunks)
        _assert_unique_analyst_views(self.analyst_views)
        return self


class CritiqueObjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objection_id: str
    category: Literal[
        "analyst_contradiction",
        "unsupported_claim",
        "violated_hard_rule",
        "stale_or_incomplete_data",
        "risk_number_mismatch",
        "internal_inconsistency",
    ]
    severity: Literal["low", "medium", "high"]
    source: Literal["deterministic", "model"]
    claim: str
    metric_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[int] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_objection(self) -> "CritiqueObjection":
        if not self.claim.strip():
            raise ValueError("Objection claim must not be empty.")
        if self.severity in {"medium", "high"} and not (
            self.metric_ids or self.chunk_ids or self.warning_codes
        ):
            raise ValueError("Medium/high-severity objections must include supporting references.")
        return self


class CritiqueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    context_hash: str
    agent_name: Literal["critic_guardrail"] = "critic_guardrail"
    status: Literal["completed", "insufficient_data", "failed"]
    verdict: Literal["accept", "revise", "reject"]
    objections: list[CritiqueObjection] = Field(default_factory=list)
    proposed_decision: Optional[Literal["trade", "watchlist", "no_trade"]] = None
    proposed_confidence_penalty: float = Field(default=0.0, ge=0, le=1)
    revision_instructions: str = ""
    summary: str

    @model_validator(mode="after")
    def _validate_response(self) -> "CritiqueResponse":
        _assert_unique_objection_ids(self.objections)
        if not self.summary.strip():
            raise ValueError("Critique summary must not be empty.")
        if self.status in {"insufficient_data", "failed"}:
            if self.verdict != "accept":
                raise ValueError("Failed and insufficient-data critiques must keep verdict 'accept'.")
            if self.objections:
                raise ValueError("Failed and insufficient-data critiques must not include objections.")
            if self.proposed_confidence_penalty != 0.0:
                raise ValueError("Failed and insufficient-data critiques must keep confidence penalty 0.0.")
            if self.proposed_decision is not None:
                raise ValueError("Failed and insufficient-data critiques must not propose a decision change.")
        if self.verdict == "revise" and not self.revision_instructions.strip():
            raise ValueError("Revision verdicts must include revision instructions.")
        if self.verdict == "reject" and not any(
            objection.severity == "high" for objection in self.objections
        ):
            raise ValueError("Reject verdicts must include at least one high-severity objection.")
        return self


class CritiqueValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    reason_codes: list[str] = Field(default_factory=list)


class CritiqueResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response: CritiqueResponse
    validation: CritiqueValidation
    validation_diagnostics: list[str] = Field(default_factory=list)
    raw_output: str = ""
    latency_ms: float = Field(ge=0, default=0.0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    model_name: str
    prompt_version: str


class CriticVersionStamp(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_schema_version: str
    critique_schema_version: str
    prompt_version: str
    model_name: str
    policy_version: str


class CriticEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "shadow", "enforced"]
    accepted: bool
    status: Literal["completed", "insufficient_data", "failed"]
    verdict: Literal["accept", "revise", "reject"]
    reason_codes: list[str] = Field(default_factory=list)
    objection_counts: dict[str, int] = Field(default_factory=dict)
    deterministic_objection_ids: list[str] = Field(default_factory=list)
    model_objection_ids: list[str] = Field(default_factory=list)
    baseline_decision: Literal["trade", "watchlist", "no_trade"]
    proposed_decision: Optional[Literal["trade", "watchlist", "no_trade"]] = None
    final_decision: Literal["trade", "watchlist", "no_trade"]
    decision_changed: bool
    baseline_confidence: float = Field(ge=0, le=1)
    applied_confidence_penalty: float = Field(ge=0, le=1)
    final_confidence: float = Field(ge=0, le=1)
    revisions_requested: int = Field(ge=0)
    revisions_performed: int = Field(ge=0)
    versions: CriticVersionStamp


def _assert_unique_chunk_ids(chunks: list[EvidenceChunkInput]) -> None:
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("Evidence chunk IDs must be unique.")


def _assert_unique_analyst_views(views: list[AnalystView]) -> None:
    agent_names = [view.agent_name for view in views]
    if len(agent_names) != len(set(agent_names)):
        raise ValueError("Each analyst may appear at most once in a critique request.")


def _assert_unique_objection_ids(objections: list[CritiqueObjection]) -> None:
    objection_ids = [objection.objection_id for objection in objections]
    if len(objection_ids) != len(set(objection_ids)):
        raise ValueError("Objection IDs must be unique across the full critique response.")


_DecisionDiagnosis = getattr(import_module("app.schemas.analyze"), "DecisionDiagnosis")

CritiqueRequest.model_rebuild(_types_namespace={"DecisionDiagnosis": _DecisionDiagnosis})
