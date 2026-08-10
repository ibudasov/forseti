"""Ingestor protocol and shared data types."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Protocol

from app.db.models import SourceType


@dataclass
class RawDocument:
    ticker: str
    source_type: SourceType
    source_url: str
    text: str
    published_at: Optional[datetime] = None


def compute_source_hash(source_url: str, chunk_index: int, text: str) -> str:
    """Deterministic hash used for idempotency de-duplication."""
    payload = f"{source_url}::{chunk_index}::{text}"
    return hashlib.sha256(payload.encode()).hexdigest()


class Ingestor(Protocol):
    """Common protocol for all source-specific ingestors."""

    def fetch(self, ticker: str) -> List[RawDocument]:
        """Fetch raw documents for *ticker*."""
        ...
