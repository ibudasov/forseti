"""News ingestor — company and sector news via yfinance."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import List

from app.db.models import SourceQualityTier, SourceType
from app.rag.ingestion.base import IngestionResult, RawDocument, SourceCoverage

logger = logging.getLogger(__name__)


def _news_document_id(source_type: SourceType, url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return f"{source_type.value}:{digest}"


def _parse_yf_news(ticker: str, articles: list, source_type: SourceType) -> List[RawDocument]:
    documents: List[RawDocument] = []
    for article in articles:
        content = article.get("content", {})
        title = content.get("title", "")
        body = content.get("body") or content.get("summary") or title
        url = content.get("canonicalUrl", {}).get("url") or article.get("link", "")
        publisher = (
            content.get("provider", {}).get("displayName")
            or content.get("provider", {}).get("name")
            or article.get("publisher")
            or "Yahoo Finance"
        )
        published_at_ts = content.get("pubDate")
        published_at: datetime | None = None
        if published_at_ts:
            try:
                published_at = datetime.fromisoformat(published_at_ts.replace("Z", "+00:00"))
            except Exception:
                published_at = datetime.now(timezone.utc)

        text = f"{title}\n\n{body}".strip()
        if text and url:
            documents.append(
                RawDocument(
                    ticker=ticker.upper(),
                    source_type=source_type,
                    document_id=_news_document_id(source_type, url),
                    source_url=url,
                    publisher=publisher,
                    title=title or publisher,
                    text=text,
                    source_quality_tier=SourceQualityTier.secondary_reputable,
                    published_at=published_at,
                )
            )
    return documents


class CompanyNewsIngestor:
    """Fetches recent company news for a ticker via yfinance."""

    def fetch(self, ticker: str) -> IngestionResult:
        try:
            import yfinance as yf

            info = yf.Ticker(ticker)
            articles = info.news or []
            documents = _parse_yf_news(ticker, articles, SourceType.company_news)
            return IngestionResult(
                documents=documents,
                coverage=[
                    SourceCoverage(
                        ticker=ticker.upper(),
                        source_type=SourceType.company_news,
                        source_quality_tier=SourceQualityTier.secondary_reputable,
                        status="available" if documents else "missing",
                        detail="yfinance_news",
                        document_count=len(documents),
                        latest_published_at=max(
                            (document.published_at for document in documents if document.published_at),
                            default=None,
                        ),
                    )
                ],
            )
        except Exception as exc:
            logger.warning("Company news fetch failed for %s: %s", ticker, exc)
            return IngestionResult(
                documents=[],
                coverage=[
                    SourceCoverage(
                        ticker=ticker.upper(),
                        source_type=SourceType.company_news,
                        source_quality_tier=SourceQualityTier.secondary_reputable,
                        status="provider_error",
                        detail=str(exc),
                    )
                ],
            )


class SectorNewsIngestor:
    """Fetches recent sector-level news for a set of sector tickers."""

    SECTOR_PROXY_TICKERS = {
        "ai": "NVDA",
        "defence": "LMT",
        "nuclear": "CEG",
        "green_energy": "ENPH",
        "quantum": "IONQ",
        "robotics": "IRBT",
        "space": "RKLB",
    }

    def __init__(self, sector: str) -> None:
        self._sector = sector
        self._proxy_ticker = self.SECTOR_PROXY_TICKERS.get(sector, sector)

    def fetch(self, ticker: str) -> IngestionResult:
        """Fetch sector news using a sector proxy ticker; tag with *ticker*."""
        try:
            import yfinance as yf

            info = yf.Ticker(self._proxy_ticker)
            articles = info.news or []
            docs = _parse_yf_news(ticker, articles, SourceType.sector_news)
            return IngestionResult(
                documents=docs,
                coverage=[
                    SourceCoverage(
                        ticker=ticker.upper(),
                        source_type=SourceType.sector_news,
                        source_quality_tier=SourceQualityTier.secondary_reputable,
                        status="available" if docs else "missing",
                        detail=f"sector_proxy:{self._proxy_ticker}",
                        document_count=len(docs),
                        latest_published_at=max(
                            (document.published_at for document in docs if document.published_at),
                            default=None,
                        ),
                    )
                ],
            )
        except Exception as exc:
            logger.warning("Sector news fetch failed for %s/%s: %s", ticker, self._sector, exc)
            return IngestionResult(
                documents=[],
                coverage=[
                    SourceCoverage(
                        ticker=ticker.upper(),
                        source_type=SourceType.sector_news,
                        source_quality_tier=SourceQualityTier.secondary_reputable,
                        status="provider_error",
                        detail=str(exc),
                    )
                ],
            )
