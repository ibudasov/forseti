"""News ingestor — company and sector news via yfinance."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.db.models import SourceType
from app.rag.ingestion.base import RawDocument

logger = logging.getLogger(__name__)


def _parse_yf_news(ticker: str, articles: list, source_type: SourceType) -> List[RawDocument]:
    documents: List[RawDocument] = []
    for article in articles:
        content = article.get("content", {})
        title = content.get("title", "")
        body = content.get("body") or content.get("summary") or title
        url = content.get("canonicalUrl", {}).get("url") or article.get("link", "")
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
                    source_url=url,
                    text=text,
                    published_at=published_at,
                )
            )
    return documents


class CompanyNewsIngestor:
    """Fetches recent company news for a ticker via yfinance."""

    def fetch(self, ticker: str) -> List[RawDocument]:
        try:
            import yfinance as yf

            info = yf.Ticker(ticker)
            articles = info.news or []
            return _parse_yf_news(ticker, articles, SourceType.company_news)
        except Exception as exc:
            logger.warning("Company news fetch failed for %s: %s", ticker, exc)
            return []


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

    def fetch(self, ticker: str) -> List[RawDocument]:
        """Fetch sector news using a sector proxy ticker; tag with *ticker*."""
        try:
            import yfinance as yf

            info = yf.Ticker(self._proxy_ticker)
            articles = info.news or []
            docs = _parse_yf_news(ticker, articles, SourceType.sector_news)
            # Override source_url prefix to include sector context
            return docs
        except Exception as exc:
            logger.warning("Sector news fetch failed for %s/%s: %s", ticker, self._sector, exc)
            return []
