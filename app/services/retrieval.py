"""Retrieval service for RAG - retrieves relevant evidence chunks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlmodel import Session, select, text

from app.db.models import DocumentChunk, DocumentSourceType
from app.db.session import get_engine, get_session


class RetrievalResult:
    """Represents a single retrieved chunk with relevance metadata."""
    
    def __init__(
        self,
        chunk_id: int,
        ticker: str,
        source_type: DocumentSourceType,
        source_url: str,
        published_at: Optional[datetime],
        ingested_at: datetime,
        text: str,
        similarity_score: Optional[float] = None,
    ):
        self.chunk_id = chunk_id
        self.ticker = ticker
        self.source_type = source_type
        self.source_url = source_url
        self.published_at = published_at
        self.ingested_at = ingested_at
        self.text = text
        self.similarity_score = similarity_score

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "chunk_id": self.chunk_id,
            "ticker": self.ticker,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "ingested_at": self.ingested_at.isoformat(),
            "text": self.text,
            "similarity_score": self.similarity_score,
        }


def retrieve(
    ticker: str,
    top_k: int = 5,
    source_types: Optional[List[DocumentSourceType]] = None,
    days_lookback: Optional[int] = None,
    engine=None,
) -> List[RetrievalResult]:
    """
    Retrieve the most relevant document chunks for a ticker.
    
    Args:
        ticker: The stock ticker to retrieve evidence for
        top_k: Number of top results to return
        source_types: Optional filter by source types (e.g., earnings_call, company_news)
        days_lookback: Optional filter to only include documents from the last N days
        engine: Database engine (uses default if None)
    
    Returns:
        List of RetrievalResult objects, ordered by recency and relevance
    """
    engine = engine or get_engine()
    ticker = ticker.strip().upper()
    
    stmt = select(DocumentChunk).where(DocumentChunk.ticker == ticker)
    
    if source_types:
        stmt = stmt.where(DocumentChunk.source_type.in_(source_types))
    
    if days_lookback:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_lookback)
        stmt = stmt.where(DocumentChunk.ingested_at >= cutoff_date)
    
    # Order by ingested_at descending (most recent first), then by id for determinism
    stmt = stmt.order_by(
        DocumentChunk.ingested_at.desc(),
        DocumentChunk.id.desc(),
    ).limit(top_k)
    
    with get_session(engine) as session:
        chunks = session.exec(stmt).all()
    
    return [
        RetrievalResult(
            chunk_id=chunk.id,
            ticker=chunk.ticker,
            source_type=chunk.source_type,
            source_url=chunk.source_url,
            published_at=chunk.published_at,
            ingested_at=chunk.ingested_at,
            text=chunk.text,
            similarity_score=None,  # Placeholder for vector similarity
        )
        for chunk in chunks
    ]


def retrieve_by_source_type(
    ticker: str,
    source_type: DocumentSourceType,
    top_k: int = 5,
    days_lookback: Optional[int] = None,
    engine=None,
) -> List[RetrievalResult]:
    """
    Retrieve the most relevant document chunks for a ticker and specific source type.
    
    Args:
        ticker: The stock ticker to retrieve evidence for
        source_type: The type of source to filter by
        top_k: Number of top results to return
        days_lookback: Optional filter to only include documents from the last N days
        engine: Database engine (uses default if None)
    
    Returns:
        List of RetrievalResult objects, ordered by recency
    """
    return retrieve(
        ticker=ticker,
        top_k=top_k,
        source_types=[source_type],
        days_lookback=days_lookback,
        engine=engine,
    )
