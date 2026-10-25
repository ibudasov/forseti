from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SYNTHESIZER_CONTRACT = Literal["legacy", "typed"]
MEMO_DECISION = Literal["trade", "watchlist", "no_trade"]


class DecisionMemoItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim: str
    metric_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[int] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_claim(self) -> "DecisionMemoItem":
        if not self.claim.strip():
            raise ValueError("Memo claims must not be empty.")
        return self


class DecisionMemo(BaseModel):
    """Validated, cited memo exposed to API users; the risk block is rendered from rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thesis: str
    key_reasons: list[DecisionMemoItem] = Field(default_factory=list)
    key_risks: list[DecisionMemoItem] = Field(default_factory=list)
    what_would_change: list[DecisionMemoItem] = Field(default_factory=list)
    risk_block: str


class SynthesizerEffect(BaseModel):
    """Persisted outcome of the typed Decision Synthesizer for one agent run."""

    model_config = ConfigDict(extra="forbid")

    contract: SYNTHESIZER_CONTRACT
    prompt_version: str
    fallback_used: bool
    proposed_decision: MEMO_DECISION
    final_decision: MEMO_DECISION
    validation_failures: list[str] = Field(default_factory=list)
    memo_hash: str
