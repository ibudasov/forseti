"""Schemas for evidence/RAG API endpoints."""
from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from pydantic import BaseModel, Field


class EvidenceItemSchema(BaseModel):
    """Single piece of evidence with source citation."""
    text: str = Field(..., description="Evidence text excerpt")
    chunk_id: int = Field(..., description="ID of the source chunk")
    source_url: str = Field(..., description="URL to the source document")
    published_at: Optional[str] = Field(None, description="ISO timestamp of publication")


class EvidenceBlockSchema(BaseModel):
    """Evidence block in analysis response."""
    bullish_drivers: List[EvidenceItemSchema] = Field(default_factory=list)
    bearish_risks: List[EvidenceItemSchema] = Field(default_factory=list)
    catalysts: List[EvidenceItemSchema] = Field(default_factory=list)
    news_alignment: str = Field("neutral", description="supporting, neutral, or weakening")
    red_flags: List[EvidenceItemSchema] = Field(default_factory=list)
    confidence_adjustment: Optional[float] = Field(None, description="Confidence adjustment -0.1 to +0.1")
    status: str = Field("complete", description="complete, insufficient_data, or error")


class TickerEvidenceResponse(BaseModel):
    """Response for GET /ticker/{symbol}/evidence endpoint."""
    ticker: str = Field(..., description="Stock ticker")
    evidence: EvidenceBlockSchema = Field(...)
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)
