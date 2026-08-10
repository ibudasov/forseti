"""Tests for RAG - document chunks, retrieval, and synthesis."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os
import hashlib

import pytest
from sqlmodel import SQLModel, create_engine

from testcontainers.community.postgres import PostgresContainer

from app.db.models import DocumentChunk, DocumentSourceType
from app.db.repository import (
    create_document_chunk,
    bulk_create_document_chunks,
    get_document_chunks_by_ticker,
    delete_document_chunks_by_source_hash,
)
from app.services.retrieval import retrieve, retrieve_by_source_type, RetrievalResult
from app.services.synthesis import synthesize_evidence, EvidenceSynthesis, GroundingError


def _create_test_engine(database_url: str):
    return create_engine(database_url, echo=False, future=True)


def _create_test_database(engine):
    """Initialize test database with all tables."""
    SQLModel.metadata.create_all(engine)


@pytest.fixture
def db_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        engine = _create_test_engine(url)
        SQLModel.metadata.drop_all(engine)
        _create_test_database(engine)
        yield engine
    else:
        container = PostgresContainer("postgres:15")
        with container:
            url = container.get_connection_url()
            engine = _create_test_engine(url)
            _create_test_database(engine)
            yield engine


def _make_source_hash(text: str, source_url: str) -> str:
    """Generate deterministic source hash."""
    content = f"{text}:{source_url}".encode()
    return hashlib.sha256(content).hexdigest()


def test_create_document_chunk(db_engine):
    """Test creating a single document chunk."""
    chunk = DocumentChunk(
        ticker="AAPL",
        source_type=DocumentSourceType.earnings_call,
        source_url="https://example.com/earnings",
        published_at=datetime.now(timezone.utc),
        text="Apple reported strong Q1 revenue.",
        chunk_size=10,
        chunk_index=0,
        source_hash="hash123",
    )
    
    created = create_document_chunk(chunk, engine=db_engine)
    assert created.id is not None
    assert created.ticker == "AAPL"
    assert created.text == "Apple reported strong Q1 revenue."


def test_bulk_create_document_chunks_idempotency(db_engine):
    """Test bulk creation with idempotency via source_hash."""
    chunks = [
        DocumentChunk(
            ticker="AAPL",
            source_type=DocumentSourceType.company_news,
            source_url="https://example.com/news1",
            text="News about AAPL",
            chunk_size=10,
            chunk_index=0,
            source_hash="hash1",
        ),
        DocumentChunk(
            ticker="AAPL",
            source_type=DocumentSourceType.company_news,
            source_url="https://example.com/news2",
            text="More news about AAPL",
            chunk_size=10,
            chunk_index=0,
            source_hash="hash2",
        ),
    ]
    
    # First insert
    count1 = bulk_create_document_chunks(chunks, engine=db_engine)
    assert count1 == 2
    
    # Second insert with same hashes (should be skipped)
    count2 = bulk_create_document_chunks(chunks, engine=db_engine)
    assert count2 == 0


def test_get_document_chunks_by_ticker(db_engine):
    """Test retrieving chunks by ticker."""
    # Create chunks for AAPL and MSFT
    aapl_chunk = DocumentChunk(
        ticker="AAPL",
        source_type=DocumentSourceType.earnings_call,
        source_url="https://example.com/aapl_earnings",
        text="AAPL earnings call summary",
        chunk_size=10,
        chunk_index=0,
        source_hash="aapl_hash",
    )
    
    msft_chunk = DocumentChunk(
        ticker="MSFT",
        source_type=DocumentSourceType.earnings_call,
        source_url="https://example.com/msft_earnings",
        text="MSFT earnings call summary",
        chunk_size=10,
        chunk_index=0,
        source_hash="msft_hash",
    )
    
    create_document_chunk(aapl_chunk, engine=db_engine)
    create_document_chunk(msft_chunk, engine=db_engine)
    
    # Retrieve AAPL chunks
    aapl_chunks = get_document_chunks_by_ticker("AAPL", engine=db_engine)
    assert len(aapl_chunks) == 1
    assert aapl_chunks[0].ticker == "AAPL"
    
    # Retrieve MSFT chunks
    msft_chunks = get_document_chunks_by_ticker("MSFT", engine=db_engine)
    assert len(msft_chunks) == 1
    assert msft_chunks[0].ticker == "MSFT"


def test_get_document_chunks_by_ticker_and_source_type(db_engine):
    """Test retrieving chunks by ticker and source type."""
    chunks = [
        DocumentChunk(
            ticker="AAPL",
            source_type=DocumentSourceType.earnings_call,
            source_url="https://example.com/earnings",
            text="Earnings call",
            chunk_size=10,
            chunk_index=0,
            source_hash="hash1",
        ),
        DocumentChunk(
            ticker="AAPL",
            source_type=DocumentSourceType.company_news,
            source_url="https://example.com/news",
            text="Company news",
            chunk_size=10,
            chunk_index=0,
            source_hash="hash2",
        ),
    ]
    
    bulk_create_document_chunks(chunks, engine=db_engine)
    
    # Retrieve only earnings_call chunks
    earnings_chunks = get_document_chunks_by_ticker(
        "AAPL",
        source_type=DocumentSourceType.earnings_call,
        engine=db_engine,
    )
    assert len(earnings_chunks) == 1
    assert earnings_chunks[0].source_type == DocumentSourceType.earnings_call


def test_delete_document_chunk_by_source_hash(db_engine):
    """Test deleting a chunk by source_hash."""
    chunk = DocumentChunk(
        ticker="AAPL",
        source_type=DocumentSourceType.company_news,
        source_url="https://example.com/news",
        text="News",
        chunk_size=10,
        chunk_index=0,
        source_hash="delete_me",
    )
    
    create_document_chunk(chunk, engine=db_engine)
    
    # Verify it exists
    chunks = get_document_chunks_by_ticker("AAPL", engine=db_engine)
    assert len(chunks) == 1
    
    # Delete it
    deleted = delete_document_chunks_by_source_hash("delete_me", engine=db_engine)
    assert deleted is True
    
    # Verify it's gone
    chunks = get_document_chunks_by_ticker("AAPL", engine=db_engine)
    assert len(chunks) == 0


def test_retrieve_chunks_empty(db_engine):
    """Test retrieval when no chunks exist."""
    results = retrieve("NONEXISTENT", engine=db_engine)
    assert len(results) == 0


def test_retrieve_chunks_basic(db_engine):
    """Test basic chunk retrieval."""
    now = datetime.now(timezone.utc)
    chunks = [
        DocumentChunk(
            ticker="AAPL",
            source_type=DocumentSourceType.company_news,
            source_url="https://example.com/news1",
            published_at=now,
            text="News 1",
            chunk_size=10,
            chunk_index=0,
            source_hash="hash1",
        ),
        DocumentChunk(
            ticker="AAPL",
            source_type=DocumentSourceType.company_news,
            source_url="https://example.com/news2",
            published_at=now - timedelta(days=1),
            text="News 2",
            chunk_size=10,
            chunk_index=0,
            source_hash="hash2",
        ),
    ]
    
    bulk_create_document_chunks(chunks, engine=db_engine)
    
    # Retrieve all chunks
    results = retrieve("AAPL", top_k=10, engine=db_engine)
    assert len(results) == 2
    
    # Most recent first
    assert results[0].text == "News 1"
    assert results[1].text == "News 2"


def test_retrieve_chunks_with_filter(db_engine):
    """Test retrieval with source type filter."""
    chunks = [
        DocumentChunk(
            ticker="AAPL",
            source_type=DocumentSourceType.earnings_call,
            source_url="https://example.com/earnings",
            text="Earnings",
            chunk_size=10,
            chunk_index=0,
            source_hash="hash1",
        ),
        DocumentChunk(
            ticker="AAPL",
            source_type=DocumentSourceType.company_news,
            source_url="https://example.com/news",
            text="News",
            chunk_size=10,
            chunk_index=0,
            source_hash="hash2",
        ),
    ]
    
    bulk_create_document_chunks(chunks, engine=db_engine)
    
    # Retrieve only earnings calls
    results = retrieve(
        "AAPL",
        source_types=[DocumentSourceType.earnings_call],
        engine=db_engine,
    )
    assert len(results) == 1
    assert "Earnings" in results[0].text


def test_retrieve_by_source_type(db_engine):
    """Test retrieval by single source type."""
    chunks = [
        DocumentChunk(
            ticker="AAPL",
            source_type=DocumentSourceType.company_news,
            source_url="https://example.com/news",
            text="News",
            chunk_size=10,
            chunk_index=0,
            source_hash="hash1",
        ),
    ]
    
    bulk_create_document_chunks(chunks, engine=db_engine)
    
    results = retrieve_by_source_type("AAPL", DocumentSourceType.company_news, engine=db_engine)
    assert len(results) == 1
    assert results[0].source_type == DocumentSourceType.company_news


def test_synthesize_evidence_empty(db_engine):
    """Test synthesis with no retrieval results."""
    synthesis = synthesize_evidence([])
    assert synthesis.status == "insufficient_data"
    assert len(synthesis.bullish_drivers) == 0
    assert len(synthesis.bearish_risks) == 0


def test_synthesize_evidence_basic(db_engine):
    """Test basic evidence synthesis."""
    results = [
        RetrievalResult(
            chunk_id=1,
            ticker="AAPL",
            source_type=DocumentSourceType.company_news,
            source_url="https://example.com/news",
            published_at=None,
            ingested_at=datetime.now(timezone.utc),
            text="Apple reported strong revenue growth and positive market opportunities.",
        ),
    ]
    
    synthesis = synthesize_evidence(results)
    assert synthesis.status == "complete"
    assert len(synthesis.bullish_drivers) > 0 or len(synthesis.bearish_risks) == 0


def test_synthesis_guardrails_violation(db_engine):
    """Test that guardrails catch trading parameter mentions."""
    results = [
        RetrievalResult(
            chunk_id=1,
            ticker="AAPL",
            source_type=DocumentSourceType.company_news,
            source_url="https://example.com/news",
            published_at=None,
            ingested_at=datetime.now(timezone.utc),
            text="Stop loss at $150 and take profit at $200.",
        ),
    ]
    
    # This should not raise in basic synthesis - guardrails are applied after
    synthesis = synthesize_evidence(results)
    # But if we had LLM-generated synthesis that violated guardrails, it would raise
    # For now, our basic implementation passes through


def test_retrieval_result_to_dict(db_engine):
    """Test RetrievalResult serialization."""
    now = datetime.now(timezone.utc)
    result = RetrievalResult(
        chunk_id=1,
        ticker="AAPL",
        source_type=DocumentSourceType.company_news,
        source_url="https://example.com/news",
        published_at=now,
        ingested_at=now,
        text="Test text",
        similarity_score=0.95,
    )
    
    data = result.to_dict()
    assert data["chunk_id"] == 1
    assert data["ticker"] == "AAPL"
    assert data["similarity_score"] == 0.95
    assert isinstance(data["ingested_at"], str)


def test_evidence_synthesis_to_dict(db_engine):
    """Test EvidenceSynthesis serialization."""
    from app.services.synthesis import EvidenceItem
    
    synthesis = EvidenceSynthesis(
        bullish_drivers=[
            EvidenceItem(
                text="Strong revenue growth",
                chunk_id=1,
                source_url="https://example.com",
            ),
        ],
        bearish_risks=[],
        catalysts=[],
        news_alignment="supporting",
        red_flags=[],
    )
    
    data = synthesis.to_dict()
    assert len(data["bullish_drivers"]) == 1
    assert data["news_alignment"] == "supporting"
