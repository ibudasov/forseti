from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.analyzer import validate_and_normalize_ticker


class AnalyzeRequest(BaseModel):
    ticker: str
    account_size_eur: Optional[float] = Field(default=None, gt=0)
    risk_percentage: Optional[float] = Field(default=None, gt=0, le=1)
    max_position_size_eur: Optional[float] = Field(default=None, gt=0)
    as_of_date: Optional[date] = None
    notes: Optional[str] = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return validate_and_normalize_ticker(value)


class EvidenceItemResponse(BaseModel):
    claim: str
    chunk_ids: List[int] = Field(default_factory=list)


class EvidenceBlock(BaseModel):
    bullish_drivers: List[EvidenceItemResponse] = Field(default_factory=list)
    bearish_risks: List[EvidenceItemResponse] = Field(default_factory=list)
    catalysts: List[EvidenceItemResponse] = Field(default_factory=list)
    news_alignment: str = ""
    red_flags: List[EvidenceItemResponse] = Field(default_factory=list)
    chunk_count: int = 0
    status: str = "ok"


class TraceStep(BaseModel):
    sequence: int
    agent_name: str
    status: str
    tool_calls: List[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)
    retries: int = 0
    output: Optional[dict[str, Any]] = None


class AnalysisTrace(BaseModel):
    run_id: str
    ticker: str
    steps: List[TraceStep] = Field(default_factory=list)
    final_decision: Optional[str] = None
    total_latency_ms: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    ticker: str
    decision: Literal["trade", "watchlist", "no_trade"]
    entry_range: Optional[Tuple[float, float]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[Tuple[float, float]] = None
    risk_reward: Optional[float] = None
    position_size_eur: Optional[float] = None
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
    warnings: list[str]
    engine_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    trace_id: str
    evidence: Optional[EvidenceBlock] = None
    trace: Optional[AnalysisTrace] = None

    model_config = ConfigDict(populate_by_name=True)

