"""Tests for the RAG ingestion pipeline: embedding fallback and RAG_FAIL_LOUD behavior."""
from __future__ import annotations

from argparse import Namespace

import pytest

from app.db.models import SourceType
from app.rag import pipeline
from app.rag.ingestion.base import RawDocument
from app.rag.ingestion.edgar import SECEdgarIngestor


class _RaisingEmbeddingClient:
    def embed_texts(self, texts):
        raise RuntimeError("embedding backend unavailable")


class _MockEmbeddingClient:
    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension

    def embed_texts(self, texts):
        return [[0.0] * self._dimension for _ in texts]


class _SingleDocIngestor:
    def __init__(self, ticker: str) -> None:
        self._ticker = ticker

    def fetch(self, ticker: str):
        return [
            RawDocument(
                ticker=ticker,
                source_type=SourceType.filing_business,
                source_url="https://example.com/doc",
                text="Some evidence sentence about the business. Another sentence follows.",
            )
        ]


class _EmptyIngestor:
    def fetch(self, ticker: str):
        return []


def test_sec_edgar_ingestor_uses_configured_user_agent():
    ingestor = SECEdgarIngestor("Forseti/0.1 (gazer-flair9o@icloud.com)")

    assert ingestor._session.headers["User-Agent"] == (
        "Forseti/0.1 (gazer-flair9o@icloud.com)"
    )
    ingestor._session.close()


def _settings(rag_fail_loud: bool) -> Namespace:
    return Namespace(
        CHUNK_SIZE_TOKENS=800,
        CHUNK_OVERLAP_TOKENS=100,
        EDGAR_USER_AGENT="Forseti/test",
        EMBEDDING_DIM=8,
        RAG_FAIL_LOUD=rag_fail_loud,
    )


def _patch_ingestors(monkeypatch):
    monkeypatch.setattr(pipeline, "SECEdgarIngestor", lambda user_agent: _SingleDocIngestor("NVDA"))
    monkeypatch.setattr(pipeline, "CompanyNewsIngestor", lambda: _EmptyIngestor())
    monkeypatch.setattr(pipeline, "EarningsCallIngestor", lambda: _EmptyIngestor())


class TestIngestTickerFailLoud:
    def test_embedding_failure_always_raises(self, monkeypatch):
        _patch_ingestors(monkeypatch)
        monkeypatch.setattr(pipeline, "get_settings", lambda: _settings(rag_fail_loud=False))

        stored_chunks = []
        monkeypatch.setattr(
            pipeline, "upsert_document_chunks", lambda chunks, engine=None: stored_chunks.extend(chunks)
        )

        with pytest.raises(RuntimeError, match="Google embedding failed"):
            pipeline.ingest_ticker(ticker="NVDA", embedding_client=_RaisingEmbeddingClient())
        assert stored_chunks == []

    def test_fail_loud_reraises_and_skips_upsert(self, monkeypatch):
        _patch_ingestors(monkeypatch)
        monkeypatch.setattr(pipeline, "get_settings", lambda: _settings(rag_fail_loud=True))

        upsert_called = False

        def _fail_if_called(chunks, engine=None):
            nonlocal upsert_called
            upsert_called = True

        monkeypatch.setattr(pipeline, "upsert_document_chunks", _fail_if_called)

        with pytest.raises(RuntimeError):
            pipeline.ingest_ticker(ticker="NVDA", embedding_client=_RaisingEmbeddingClient())

        assert upsert_called is False

    def test_fail_loud_preserves_success_path(self, monkeypatch):
        _patch_ingestors(monkeypatch)
        monkeypatch.setattr(pipeline, "get_settings", lambda: _settings(rag_fail_loud=True))

        stored_chunks = []
        monkeypatch.setattr(
            pipeline, "upsert_document_chunks", lambda chunks, engine=None: stored_chunks.extend(chunks)
        )

        count = pipeline.ingest_ticker(ticker="NVDA", embedding_client=_MockEmbeddingClient())

        assert count == len(stored_chunks) > 0
