"""Retrieval service: retrieve(ticker, question, top_k) → ranked DocumentChunks."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.db.models import DocumentChunk, SourceType
from app.db.repository import similarity_search
from app.rag.embedding import EmbeddingClient

logger = logging.getLogger(__name__)

_DEFAULT_DATE_WINDOW_DAYS = 365
_SECTOR_FALLBACK_SOURCE_TYPES = [SourceType.sector_news]


def retrieve(
    ticker: str,
    question: str,
    embedding_client: EmbeddingClient,
    top_k: int = 5,
    source_types: Optional[List[SourceType]] = None,
    date_window_days: int = _DEFAULT_DATE_WINDOW_DAYS,
    engine=None,
) -> List[DocumentChunk]:
    """Return the *top_k* most relevant chunks for *ticker* and *question*.

    Falls back to sector-level chunks when the ticker-specific result set is
    smaller than *top_k // 2*.
    """
    published_after = datetime.now(timezone.utc) - timedelta(days=date_window_days)
    query_embedding = embedding_client.embed_texts([question])[0]

    chunks = similarity_search(
        ticker=ticker,
        query_embedding=query_embedding,
        top_k=top_k,
        source_types=source_types,
        published_after=published_after,
        engine=engine,
    )

    if len(chunks) < top_k // 2:
        sector_chunks = similarity_search(
            ticker=ticker,
            query_embedding=query_embedding,
            top_k=top_k - len(chunks),
            source_types=_SECTOR_FALLBACK_SOURCE_TYPES,
            published_after=published_after,
            engine=engine,
        )
        existing_ids = {c.id for c in chunks}
        chunks.extend(c for c in sector_chunks if c.id not in existing_ids)

    return chunks
