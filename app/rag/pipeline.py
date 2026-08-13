"""RAG ingestion pipeline: fetch → chunk → embed → store."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Optional

from app.db.models import DocumentChunk, SourceType
from app.db.repository import upsert_document_chunks
from app.rag.chunking import chunk_text
from app.rag.embedding import EmbeddingClient
from app.rag.ingestion.base import RawDocument, compute_source_hash
from app.rag.ingestion.earnings import EarningsCallIngestor
from app.rag.ingestion.edgar import SECEdgarIngestor
from app.rag.ingestion.news import CompanyNewsIngestor, SectorNewsIngestor
from app.settings import get_settings

logger = logging.getLogger(__name__)


def _build_chunks(
    doc: RawDocument,
    chunk_size: int,
    overlap: int,
    embeddings: List[List[float]],
    text_chunks,
) -> List[DocumentChunk]:
    now = datetime.now(timezone.utc)
    result: List[DocumentChunk] = []
    for text_chunk, embedding in zip(text_chunks, embeddings):
        source_hash = compute_source_hash(doc.source_url, text_chunk.chunk_index, text_chunk.text)
        result.append(
            DocumentChunk(
                ticker=doc.ticker,
                source_type=doc.source_type,
                source_url=doc.source_url,
                source_hash=source_hash,
                published_at=doc.published_at,
                ingested_at=now,
                chunk_index=text_chunk.chunk_index,
                text=text_chunk.text,
                embedding=embedding,
            )
        )
    return result


def ingest_ticker(
    ticker: str,
    embedding_client: EmbeddingClient,
    user_agent: Optional[str] = None,
    sector: Optional[str] = None,
    engine=None,
) -> int:
    """Run the full ingestion pipeline for *ticker*; return number of new chunks stored."""
    settings = get_settings()
    chunk_size = settings.CHUNK_SIZE_TOKENS
    overlap = settings.CHUNK_OVERLAP_TOKENS
    edgar_user_agent = user_agent or settings.EDGAR_USER_AGENT

    ingestors = [
        SECEdgarIngestor(user_agent=edgar_user_agent),
        CompanyNewsIngestor(),
        EarningsCallIngestor(),
    ]
    if sector:
        ingestors.append(SectorNewsIngestor(sector=sector))

    all_chunks: List[DocumentChunk] = []
    for ingestor in ingestors:
        documents: List[RawDocument] = ingestor.fetch(ticker)
        for doc in documents:
            text_chunks = chunk_text(doc.text, chunk_size=chunk_size, overlap=overlap)
            if not text_chunks:
                continue
            texts = [tc.text for tc in text_chunks]
            try:
                embeddings = embedding_client.embed_texts(texts)
            except Exception as exc:
                logger.warning("Embedding failed for %s (%s): %s", ticker, doc.source_url, exc)
                raise RuntimeError(f"Google embedding failed for {ticker}") from exc

            all_chunks.extend(_build_chunks(doc, chunk_size, overlap, embeddings, text_chunks))

    upsert_document_chunks(all_chunks, engine=engine)
    logger.info("Ingested %d chunks for %s", len(all_chunks), ticker)
    return len(all_chunks)
