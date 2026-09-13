from __future__ import annotations
from datetime import date, datetime
from typing import Any, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.analyzer import validate_and_normalize_ticker
from app.schemas.fundamentals import FundamentalAgentEffect


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
    status: Literal["completed", "degraded", "failed", "skipped"]
    tool_calls: List[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)
    retries: int = 0
    output: Optional[dict[str, Any]] = None
    skip_reason: Optional[str] = None


class AnalysisTrace(BaseModel):
    run_id: str
    ticker: str
    steps: List[TraceStep] = Field(default_factory=list)
    final_decision: Optional[str] = None
    total_latency_ms: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    entered_agent_layer: bool = False
    adk_event_count: int = 0
    observed_agents: List[str] = Field(default_factory=list)
    fundamental_agent_effect: Optional[FundamentalAgentEffect] = None


class DecisionDiagnosis(BaseModel):
    stage: Literal["unknown_security", "data_gate", "hard_veto", "checklist", "risk_math"]
    rule_id: str
    detail: str
    checklist_score: Optional[int] = Field(default=None, ge=0, le=11)
    checklist_max: int = 11
    missing_data: List[str] = Field(default_factory=list)
    debug_reason: str


class AnalyzeResponse(BaseModel):
    ticker: str
    decision: Literal["trade", "watchlist", "no_trade"]
    time_stop_at: Optional[date] = None
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
    diagnosis: Optional[DecisionDiagnosis] = None
    fundamental_agent_effect: Optional[FundamentalAgentEffect] = None

    model_config = ConfigDict(populate_by_name=True)
