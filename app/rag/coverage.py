from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.models import SourceQualityTier, SourceType
from app.db.repository import get_security, summarize_document_coverage
from app.rag.ingestion.base import IngestionResult, SourceCoverage
from app.rag.ingestion.earnings import EarningsCallIngestor
from app.rag.ingestion.edgar import SECEdgarIngestor
from app.rag.ingestion.news import CompanyNewsIngestor, SectorNewsIngestor
from app.settings import get_settings

_EXPECTED_SOURCES: tuple[tuple[SourceType, SourceQualityTier], ...] = (
    (SourceType.filing_business, SourceQualityTier.primary_regulatory),
    (SourceType.filing_risk, SourceQualityTier.primary_regulatory),
    (SourceType.filing_mda, SourceQualityTier.primary_regulatory),
    (SourceType.earnings_release, SourceQualityTier.primary_regulatory),
    (SourceType.earnings_call_transcript, SourceQualityTier.unknown),
    (SourceType.analyst_recommendations, SourceQualityTier.secondary_reputable),
    (SourceType.earnings_calendar, SourceQualityTier.secondary_reputable),
    (SourceType.company_news, SourceQualityTier.secondary_reputable),
    (SourceType.sector_news, SourceQualityTier.secondary_reputable),
)


def build_coverage_report(
    ticker: str,
    *,
    live: bool = False,
    engine=None,
) -> dict[str, Any]:
    normalized_ticker = ticker.strip().upper()
    stored_rows = summarize_document_coverage(normalized_ticker, engine=engine)
    stored_by_source = {row["source_type"]: row for row in stored_rows}
    live_by_source = _live_coverage(normalized_ticker, engine=engine) if live else {}

    sources: list[dict[str, Any]] = []
    for source_type, default_quality in _EXPECTED_SOURCES:
        stored_row = stored_by_source.get(source_type.value, {})
        live_row = live_by_source.get(source_type.value)
        sources.append(
            {
                "source_type": source_type.value,
                "quality_tier": stored_row.get("source_quality_tier", default_quality.value),
                "chunk_count": stored_row.get("chunk_count", 0),
                "document_count": stored_row.get("document_count", 0),
                "latest_published_at": _isoformat(stored_row.get("latest_published_at")),
                "latest_ingested_at": _isoformat(stored_row.get("latest_ingested_at")),
                "status": live_row.status if live_row is not None else ("available" if stored_row else "missing"),
                "detail": live_row.detail if live_row is not None else ("stored_chunks_present" if stored_row else "not_ingested"),
            }
        )

    return {
        "ticker": normalized_ticker,
        "live_probe": live,
        "sources": sources,
    }


def _live_coverage(ticker: str, *, engine=None) -> dict[str, SourceCoverage]:
    settings = get_settings()
    security = get_security(ticker, engine=engine)
    sector = None if security is None else security.sector_tag.value
    ingestors = [
        SECEdgarIngestor(user_agent=settings.EDGAR_USER_AGENT),
        CompanyNewsIngestor(),
        EarningsCallIngestor(transcript_url_template=settings.EARNINGS_TRANSCRIPT_URL_TEMPLATE),
    ]
    if sector:
        ingestors.append(SectorNewsIngestor(sector=sector))

    coverage: dict[str, SourceCoverage] = {}
    for ingestor in ingestors:
        result: IngestionResult = ingestor.fetch(ticker)
        for note in result.coverage:
            coverage[note.source_type.value] = note
    return coverage


def _isoformat(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None
