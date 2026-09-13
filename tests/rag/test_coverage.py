from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import DocumentChunk, SourceQualityTier, SourceType
from app.db.repository import upsert_document_chunks
from app.rag.coverage import build_coverage_report
from app.rag.ingestion.base import SourceCoverage


def test_build_coverage_report_combines_stored_and_live_results(db_engine, monkeypatch):
    chunk = DocumentChunk(
        ticker="NVDA",
        source_type=SourceType.filing_business,
        document_id="sec:0001",
        source_url="https://example.com/10k",
        publisher="Example Corp",
        title="NVDA 10-K Business",
        form_type="10-K",
        accession_number="0001",
        period_end=datetime(2025, 12, 31, tzinfo=timezone.utc).date(),
        source_quality_tier=SourceQualityTier.primary_regulatory,
        source_hash="hash-10k",
        published_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        chunk_index=0,
        text="Business section text",
        embedding=[0.0] * 768,
    )
    upsert_document_chunks([chunk], engine=db_engine)
    monkeypatch.setattr(
        "app.rag.coverage._live_coverage",
        lambda ticker, engine=None: {
            SourceType.filing_business.value: SourceCoverage(
                ticker=ticker,
                source_type=SourceType.filing_business,
                source_quality_tier=SourceQualityTier.primary_regulatory,
                status="available",
                detail="section_extracted:10-K",
                document_count=1,
                latest_published_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            ),
            SourceType.earnings_call_transcript.value: SourceCoverage(
                ticker=ticker,
                source_type=SourceType.earnings_call_transcript,
                source_quality_tier=SourceQualityTier.unknown,
                status="unavailable",
                detail="earnings_call_transcript_unavailable",
            ),
        },
    )

    report = build_coverage_report("NVDA", live=True, engine=db_engine)

    business_row = next(row for row in report["sources"] if row["source_type"] == SourceType.filing_business.value)
    transcript_row = next(
        row for row in report["sources"] if row["source_type"] == SourceType.earnings_call_transcript.value
    )
    assert business_row["chunk_count"] == 1
    assert business_row["status"] == "available"
    assert transcript_row["status"] == "unavailable"
