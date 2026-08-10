"""Earnings call ingestor — summaries from yfinance analyst recommendations."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.db.models import SourceType
from app.rag.ingestion.base import RawDocument

logger = logging.getLogger(__name__)


class EarningsCallIngestor:
    """Fetches earnings-related analyst summaries for a ticker via yfinance."""

    def fetch(self, ticker: str) -> List[RawDocument]:
        try:
            import yfinance as yf

            info = yf.Ticker(ticker)
            return self._build_documents(ticker, info)
        except Exception as exc:
            logger.warning("Earnings call fetch failed for %s: %s", ticker, exc)
            return []

    def _build_documents(self, ticker: str, info) -> List[RawDocument]:
        documents: List[RawDocument] = []

        # Analyst recommendations summary
        try:
            recommendations = info.recommendations
            if recommendations is not None and not recommendations.empty:
                summary_lines = ["Analyst recommendations summary:"]
                for _, row in recommendations.tail(10).iterrows():
                    summary_lines.append(
                        f"  Period: {row.get('period', 'N/A')} — "
                        f"strongBuy={row.get('strongBuy', 0)} buy={row.get('buy', 0)} "
                        f"hold={row.get('hold', 0)} sell={row.get('sell', 0)} "
                        f"strongSell={row.get('strongSell', 0)}"
                    )
                text = "\n".join(summary_lines)
                documents.append(
                    RawDocument(
                        ticker=ticker.upper(),
                        source_type=SourceType.earnings_call,
                        source_url=f"https://finance.yahoo.com/quote/{ticker.upper()}",
                        text=text,
                        published_at=datetime.now(timezone.utc),
                    )
                )
        except Exception as exc:
            logger.debug("Recommendations unavailable for %s: %s", ticker, exc)

        # Earnings calendar
        try:
            calendar = info.calendar
            if calendar:
                cal_lines = ["Earnings calendar:"]
                for key, value in calendar.items():
                    cal_lines.append(f"  {key}: {value}")
                text = "\n".join(cal_lines)
                documents.append(
                    RawDocument(
                        ticker=ticker.upper(),
                        source_type=SourceType.earnings_call,
                        source_url=f"https://finance.yahoo.com/quote/{ticker.upper()}/financials",
                        text=text,
                        published_at=datetime.now(timezone.utc),
                    )
                )
        except Exception as exc:
            logger.debug("Calendar unavailable for %s: %s", ticker, exc)

        return documents
