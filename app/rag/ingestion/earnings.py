"""Earnings-material ingestor with truthful source taxonomy."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

import requests

from app.db.models import SourceQualityTier, SourceType
from app.rag.ingestion.base import IngestionResult, RawDocument, SourceCoverage
from app.settings import get_settings

logger = logging.getLogger(__name__)


def _document_id(source_type: SourceType, ticker: str, suffix: str) -> str:
    digest = hashlib.sha256(f"{ticker}:{suffix}".encode()).hexdigest()[:16]
    return f"{source_type.value}:{digest}"


def _clean_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"&\w+;", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _latest_published_at(documents: List[RawDocument]) -> Optional[datetime]:
    return max((document.published_at for document in documents if document.published_at), default=None)


class EarningsCallIngestor:
    """Fetch earnings-related materials without mislabeling them as transcripts."""

    def __init__(self, transcript_url_template: str | None = None) -> None:
        self._transcript_url_template = transcript_url_template
        self._session = requests.Session()

    def fetch(self, ticker: str) -> IngestionResult:
        documents: List[RawDocument] = []
        coverage: List[SourceCoverage] = []

        try:
            import yfinance as yf

            info = yf.Ticker(ticker)
            recommendation_documents = self._build_recommendation_documents(ticker, info)
            calendar_documents = self._build_calendar_documents(ticker, info)
            documents.extend(recommendation_documents)
            documents.extend(calendar_documents)
            coverage.extend(
                [
                    self._coverage_note(
                        ticker=ticker,
                        source_type=SourceType.analyst_recommendations,
                        documents=recommendation_documents,
                        detail="yfinance_recommendations",
                    ),
                    self._coverage_note(
                        ticker=ticker,
                        source_type=SourceType.earnings_calendar,
                        documents=calendar_documents,
                        detail="yfinance_calendar",
                    ),
                ]
            )
        except Exception as exc:
            logger.warning("Earnings material fetch failed for %s: %s", ticker, exc)
            coverage.extend(
                [
                    self._error_note(ticker, SourceType.analyst_recommendations, exc),
                    self._error_note(ticker, SourceType.earnings_calendar, exc),
                ]
            )

        transcript_document, transcript_coverage = self._fetch_transcript(ticker)
        if transcript_document is not None:
            documents.append(transcript_document)
        coverage.append(transcript_coverage)
        return IngestionResult(documents=documents, coverage=coverage)

    def _build_recommendation_documents(self, ticker: str, info) -> List[RawDocument]:
        documents: List[RawDocument] = []
        try:
            recommendations = info.recommendations
            if recommendations is None or recommendations.empty:
                return []

            summary_lines = ["Analyst recommendations summary:"]
            for _, row in recommendations.tail(10).iterrows():
                summary_lines.append(
                    f"  Period: {row.get('period', 'N/A')} — "
                    f"strongBuy={row.get('strongBuy', 0)} buy={row.get('buy', 0)} "
                    f"hold={row.get('hold', 0)} sell={row.get('sell', 0)} "
                    f"strongSell={row.get('strongSell', 0)}"
                )
            documents.append(
                RawDocument(
                    ticker=ticker.upper(),
                    source_type=SourceType.analyst_recommendations,
                    document_id=_document_id(SourceType.analyst_recommendations, ticker.upper(), "recommendations"),
                    source_url=f"https://finance.yahoo.com/quote/{ticker.upper()}",
                    publisher="Yahoo Finance",
                    title=f"{ticker.upper()} analyst recommendations",
                    text="\n".join(summary_lines),
                    source_quality_tier=SourceQualityTier.secondary_reputable,
                    published_at=datetime.now(timezone.utc),
                )
            )
        except Exception as exc:
            logger.debug("Recommendations unavailable for %s: %s", ticker, exc)
        return documents

    def _build_calendar_documents(self, ticker: str, info) -> List[RawDocument]:
        documents: List[RawDocument] = []
        try:
            calendar = info.calendar
            if not calendar:
                return []
            calendar_lines = ["Earnings calendar:"]
            for key, value in calendar.items():
                calendar_lines.append(f"  {key}: {value}")
            documents.append(
                RawDocument(
                    ticker=ticker.upper(),
                    source_type=SourceType.earnings_calendar,
                    document_id=_document_id(SourceType.earnings_calendar, ticker.upper(), "calendar"),
                    source_url=f"https://finance.yahoo.com/quote/{ticker.upper()}/financials",
                    publisher="Yahoo Finance",
                    title=f"{ticker.upper()} earnings calendar",
                    text="\n".join(calendar_lines),
                    source_quality_tier=SourceQualityTier.secondary_reputable,
                    published_at=datetime.now(timezone.utc),
                )
            )
        except Exception as exc:
            logger.debug("Calendar unavailable for %s: %s", ticker, exc)
        return documents

    def _fetch_transcript(self, ticker: str) -> tuple[RawDocument | None, SourceCoverage]:
        template = self._transcript_url_template or get_settings().EARNINGS_TRANSCRIPT_URL_TEMPLATE
        if not template:
            return None, SourceCoverage(
                ticker=ticker.upper(),
                source_type=SourceType.earnings_call_transcript,
                source_quality_tier=SourceQualityTier.unknown,
                status="unavailable",
                detail="earnings_call_transcript_unavailable",
            )

        source_url = template.format(ticker=ticker.upper())
        try:
            response = self._session.get(source_url, timeout=15)
            response.raise_for_status()
            text = _clean_html(response.text)
            if not text:
                return None, SourceCoverage(
                    ticker=ticker.upper(),
                    source_type=SourceType.earnings_call_transcript,
                    source_quality_tier=SourceQualityTier.unknown,
                    status="missing",
                    detail="empty_transcript_response",
                )
            document = RawDocument(
                ticker=ticker.upper(),
                source_type=SourceType.earnings_call_transcript,
                document_id=_document_id(SourceType.earnings_call_transcript, ticker.upper(), source_url),
                source_url=source_url,
                publisher="Configured transcript source",
                title=f"{ticker.upper()} earnings call transcript",
                text=text,
                source_quality_tier=SourceQualityTier.unknown,
                published_at=datetime.now(timezone.utc),
            )
            return document, self._coverage_note(
                ticker=ticker,
                source_type=SourceType.earnings_call_transcript,
                documents=[document],
                detail="configured_transcript_source",
                quality_tier=SourceQualityTier.unknown,
            )
        except Exception as exc:
            logger.warning("Transcript fetch failed for %s: %s", ticker, exc)
            return None, self._error_note(ticker, SourceType.earnings_call_transcript, exc, SourceQualityTier.unknown)

    def _coverage_note(
        self,
        ticker: str,
        source_type: SourceType,
        documents: List[RawDocument],
        detail: str,
        quality_tier: SourceQualityTier = SourceQualityTier.secondary_reputable,
    ) -> SourceCoverage:
        return SourceCoverage(
            ticker=ticker.upper(),
            source_type=source_type,
            source_quality_tier=quality_tier,
            status="available" if documents else "missing",
            detail=detail,
            document_count=len(documents),
            latest_published_at=_latest_published_at(documents),
        )

    def _error_note(
        self,
        ticker: str,
        source_type: SourceType,
        error: Exception,
        quality_tier: SourceQualityTier = SourceQualityTier.secondary_reputable,
    ) -> SourceCoverage:
        return SourceCoverage(
            ticker=ticker.upper(),
            source_type=source_type,
            source_quality_tier=quality_tier,
            status="provider_error",
            detail=str(error),
        )
