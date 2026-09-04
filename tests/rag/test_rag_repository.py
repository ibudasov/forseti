"""Tests for DocumentChunk repository functions.

These tests require a pgvector-enabled Postgres instance.  The ``pgvector_engine``
fixture lives in ``tests/conftest.py``; without ``TEST_DATABASE_URL`` it spins up
a ``pgvector/pgvector:pg15`` container.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect
from sqlmodel import Session

from app.db.models import DocumentChunk, SourceType
from app.db.repository import upsert_document_chunks, similarity_search
from app.rag.ingestion.base import compute_source_hash


def _make_chunk(ticker: str, chunk_index: int, text: str, embedding=None) -> DocumentChunk:
    source_url = f"https://example.com/{ticker}"
    return DocumentChunk(
        ticker=ticker,
        source_type=SourceType.filing_business,
        source_url=source_url,
        source_hash=compute_source_hash(source_url, chunk_index, text),
        published_at=datetime.now(timezone.utc),
        ingested_at=datetime.now(timezone.utc),
        chunk_index=chunk_index,
        text=text,
        embedding=embedding,
    )


class TestDocumentChunkRepository:
    def test_table_exists(self, pgvector_engine):
        inspector = inspect(pgvector_engine)
        assert inspector.has_table("document_chunk")

    def test_upsert_inserts_new_chunks(self, pgvector_engine):
        chunk = _make_chunk("NVDA", 0, "NVIDIA dominates the AI GPU market.")
        upsert_document_chunks([chunk], engine=pgvector_engine)

        with Session(pgvector_engine) as session:
            from sqlmodel import select as sqlmodel_select
            rows = session.exec(sqlmodel_select(DocumentChunk).where(DocumentChunk.ticker == "NVDA")).all()
        assert len(rows) == 1
        assert rows[0].text == "NVIDIA dominates the AI GPU market."

    def test_upsert_is_idempotent(self, pgvector_engine):
        chunk = _make_chunk("AAPL", 0, "Apple has strong iPhone revenue.")
        upsert_document_chunks([chunk], engine=pgvector_engine)
        upsert_document_chunks([chunk], engine=pgvector_engine)

        with Session(pgvector_engine) as session:
            from sqlmodel import select as sqlmodel_select
            rows = session.exec(sqlmodel_select(DocumentChunk).where(DocumentChunk.ticker == "AAPL")).all()
        assert len(rows) == 1

    def test_similarity_search_returns_chunks(self, pgvector_engine):
        dim = 768
        # Insert two chunks with distinct embeddings
        embedding_a = [1.0] + [0.0] * (dim - 1)
        embedding_b = [0.0] * (dim - 1) + [1.0]

        chunk_a = _make_chunk("MSFT", 0, "Cloud growth at Microsoft.", embedding=embedding_a)
        chunk_b = _make_chunk("MSFT", 1, "Surface sales declined.", embedding=embedding_b)
        upsert_document_chunks([chunk_a, chunk_b], engine=pgvector_engine)

        # Query with embedding similar to embedding_a
        results = similarity_search(
            ticker="MSFT",
            query_embedding=embedding_a,
            top_k=1,
            engine=pgvector_engine,
        )
        assert len(results) == 1
        assert results[0].text == "Cloud growth at Microsoft."

    def test_similarity_search_no_results_for_unknown_ticker(self, pgvector_engine):
        results = similarity_search(
            ticker="UNKNOWN_XYZ_999",
            query_embedding=[0.0] * 768,
            top_k=5,
            engine=pgvector_engine,
        )
        assert results == []

    def test_similarity_search_filters_by_source_type(self, pgvector_engine):
        dim = 768
        emb = [0.5] * dim
        chunk_biz = _make_chunk("GOOG", 0, "Business section text.", embedding=emb)
        chunk_biz.source_type = SourceType.filing_business
        chunk_biz.source_hash = compute_source_hash("url_biz", 0, chunk_biz.text)

        chunk_risk = _make_chunk("GOOG", 1, "Risk factors text.", embedding=emb)
        chunk_risk.source_type = SourceType.filing_risk
        chunk_risk.source_hash = compute_source_hash("url_risk", 1, chunk_risk.text)

        upsert_document_chunks([chunk_biz, chunk_risk], engine=pgvector_engine)

        results = similarity_search(
            ticker="GOOG",
            query_embedding=emb,
            top_k=5,
            source_types=[SourceType.filing_risk],
            engine=pgvector_engine,
        )
        assert all(r.source_type == SourceType.filing_risk.value for r in results)
