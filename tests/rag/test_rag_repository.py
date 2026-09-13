"""Tests for DocumentChunk repository functions.

These tests require a pgvector-enabled Postgres instance.  The ``pgvector_engine``
fixture lives in ``tests/conftest.py``; without ``TEST_DATABASE_URL`` it spins up
a ``pgvector/pgvector:pg15`` container.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect
from sqlmodel import Session

from app.db.models import DocumentChunk, SourceQualityTier, SourceType
from app.db.repository import similarity_search, summarize_document_coverage, upsert_document_chunks
from app.rag.ingestion.base import compute_source_hash


def _make_chunk(ticker: str, chunk_index: int, text: str, embedding=None) -> DocumentChunk:
    source_url = f"https://example.com/{ticker}"
    return DocumentChunk(
        ticker=ticker,
        source_type=SourceType.filing_business,
        document_id=f"sec:{ticker}:{chunk_index}",
        source_url=source_url,
        publisher="Example Issuer",
        title=f"{ticker} filing section {chunk_index}",
        source_quality_tier=SourceQualityTier.primary_regulatory,
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

    def test_similarity_search_filters_by_quality_tier(self, pgvector_engine):
        dim = 768
        emb = [0.5] * dim
        primary_chunk = _make_chunk("QUAL", 0, "Primary regulatory text.", embedding=emb)
        primary_chunk.source_quality_tier = SourceQualityTier.primary_regulatory
        primary_chunk.source_hash = compute_source_hash("qual-primary", 0, primary_chunk.text)

        secondary_chunk = _make_chunk("QUAL", 1, "Secondary source text.", embedding=emb)
        secondary_chunk.source_quality_tier = SourceQualityTier.secondary_reputable
        secondary_chunk.source_hash = compute_source_hash("qual-secondary", 1, secondary_chunk.text)

        upsert_document_chunks([primary_chunk, secondary_chunk], engine=pgvector_engine)

        results = similarity_search(
            ticker="QUAL",
            query_embedding=emb,
            top_k=5,
            quality_tiers=[SourceQualityTier.primary_regulatory],
            engine=pgvector_engine,
        )
        assert len(results) == 1
        assert results[0].source_quality_tier == SourceQualityTier.primary_regulatory.value

    def test_summarize_document_coverage_groups_by_source_type(self, pgvector_engine):
        first_chunk = _make_chunk("COVR", 0, "Business section text.")
        second_chunk = _make_chunk("COVR", 1, "Another business section text.")
        second_chunk.document_id = first_chunk.document_id
        second_chunk.source_hash = compute_source_hash("cover-business", 1, second_chunk.text)

        risk_chunk = _make_chunk("COVR", 2, "Risk factors text.")
        risk_chunk.document_id = "sec:COVR:risk"
        risk_chunk.source_type = SourceType.filing_risk
        risk_chunk.source_hash = compute_source_hash("cover-risk", 2, risk_chunk.text)

        upsert_document_chunks([first_chunk, second_chunk, risk_chunk], engine=pgvector_engine)

        rows = summarize_document_coverage("COVR", engine=pgvector_engine)

        assert rows[0]["source_type"] == SourceType.filing_business.value
        assert rows[0]["chunk_count"] == 2
        assert rows[0]["document_count"] == 1
        assert rows[1]["source_type"] == SourceType.filing_risk.value
        assert rows[1]["chunk_count"] == 1
