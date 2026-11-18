from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from app.db.models import SourceQualityTier, SourceType
from app.rag.archive import archive_raw_document
from app.rag.ingestion.base import IngestionResult, RawDocument
from app.rag.pipeline import ingest_ticker_with_cost


def _sample_doc(
    *,
    source_type: SourceType = SourceType.company_news,
    raw_payload: bytes | str | None = '{"title":"NVDA"}',
    raw_extension: str | None = "json",
    published_at: datetime | None = datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc),
) -> RawDocument:
    return RawDocument(
        ticker="nvda",
        source_type=source_type,
        document_id="news:1",
        source_url="https://example.com/nvda-news",
        publisher="Example",
        title="NVDA Update",
        text="NVDA headline\n\nNVDA body",
        source_quality_tier=SourceQualityTier.secondary_reputable,
        published_at=published_at,
        raw_payload=raw_payload,
        raw_extension=raw_extension,
    )


def test_archive_disabled_when_dir_unset(tmp_path):
    doc = _sample_doc()
    assert archive_raw_document(doc, None) is None
    assert archive_raw_document(doc, "   ") is None
    assert list(tmp_path.iterdir()) == []


def test_archive_writes_expected_path_and_is_idempotent(tmp_path):
    doc = _sample_doc()
    payload = b'{"title":"NVDA"}'
    expected_hash = hashlib.sha256(b"https://example.com/nvda-news::" + payload).hexdigest()
    expected_path = tmp_path / "company_news" / "NVDA" / "2026-11-18" / f"{expected_hash}.json"

    first = archive_raw_document(doc, str(tmp_path))
    assert first == expected_path
    assert expected_path.read_bytes() == payload

    # Overwrite sentinel to verify second call does not rewrite existing file.
    expected_path.write_bytes(b"sentinel")
    second = archive_raw_document(doc, str(tmp_path))
    assert second == expected_path
    assert expected_path.read_bytes() == b"sentinel"


def test_archive_falls_back_to_text_and_run_date(tmp_path):
    doc = _sample_doc(
        source_type=SourceType.filing_mda,
        raw_payload=None,
        raw_extension=None,
        published_at=None,
    )
    written = archive_raw_document(doc, str(tmp_path), run_date=date(2026, 11, 17))
    assert written is not None
    assert written.suffix == ".txt"
    assert written.parts[-4:] == ("filing_mda", "NVDA", "2026-11-17", written.name)
    assert written.read_text(encoding="utf-8") == "NVDA headline\n\nNVDA body"


def test_archive_failure_logs_warning_and_returns_none(tmp_path, monkeypatch, caplog):
    doc = _sample_doc()

    def raise_oserror(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", raise_oserror)
    with caplog.at_level("WARNING"):
        result = archive_raw_document(doc, str(tmp_path))

    assert result is None
    assert "Raw document archive failed" in caplog.text


def test_pipeline_archives_before_chunking(tmp_path, monkeypatch):
    doc = _sample_doc(
        source_type=SourceType.filing_business,
        raw_payload="<html><body>Item 1. Business</body></html>",
        raw_extension="html",
    )

    class StubIngestor:
        def __init__(self, **_kwargs):
            pass

        def fetch(self, _ticker):
            return IngestionResult(documents=[doc], coverage=[])

    class EmptyIngestor:
        def __init__(self, **_kwargs):
            pass

        def fetch(self, _ticker):
            return IngestionResult(documents=[], coverage=[])

    monkeypatch.setattr("app.rag.pipeline.SECEdgarIngestor", StubIngestor)
    monkeypatch.setattr("app.rag.pipeline.CompanyNewsIngestor", EmptyIngestor)
    monkeypatch.setattr("app.rag.pipeline.EarningsCallIngestor", EmptyIngestor)
    monkeypatch.setattr("app.rag.pipeline.upsert_document_chunks", lambda chunks, engine=None: len(chunks))
    monkeypatch.setenv("RAW_DOCUMENT_DIR", str(tmp_path))

    from app.settings import get_settings

    get_settings.cache_clear()
    try:
        embedding_client = MagicMock()
        embedding_client.embed_texts.side_effect = lambda texts: [[0.1] * 4 for _ in texts]
        summary = ingest_ticker_with_cost("NVDA", embedding_client)
        assert summary.chunk_count >= 1
        archived = list(tmp_path.rglob("*.html"))
        assert len(archived) == 1
        assert archived[0].read_text(encoding="utf-8") == "<html><body>Item 1. Business</body></html>"
    finally:
        get_settings.cache_clear()
