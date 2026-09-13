"""Ingestor protocol and shared data types."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Protocol

from app.db.models import SourceQualityTier, SourceType


@dataclass
class RawDocument:
    ticker: str
    source_type: SourceType
    document_id: str
    source_url: str
    publisher: str
    title: str
    text: str
    source_quality_tier: SourceQualityTier
    published_at: Optional[datetime] = None
    form_type: Optional[str] = None
    accession_number: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None


@dataclass(frozen=True)
class SourceCoverage:
    ticker: str
    source_type: SourceType
    source_quality_tier: SourceQualityTier
    status: str
    detail: str
    document_count: int = 0
    latest_published_at: Optional[datetime] = None


@dataclass(frozen=True)
class IngestionResult:
    documents: List[RawDocument]
    coverage: List[SourceCoverage]


def compute_source_hash(source_url: str, chunk_index: int, text: str) -> str:
    """Deterministic hash used for idempotency de-duplication."""
    payload = f"{source_url}::{chunk_index}::{text}"
    return hashlib.sha256(payload.encode()).hexdigest()


class Ingestor(Protocol):
    """Common protocol for all source-specific ingestors."""

    def fetch(self, ticker: str) -> IngestionResult:
        """Fetch raw documents for *ticker*."""
        ...
