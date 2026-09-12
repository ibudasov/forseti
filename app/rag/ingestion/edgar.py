"""SEC EDGAR ingestor for filing sections and official earnings releases."""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, List

import requests

from app.db.models import SourceQualityTier, SourceType
from app.rag.ingestion.base import IngestionResult, RawDocument, SourceCoverage

logger = logging.getLogger(__name__)

EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
EDGAR_FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

_HTML_BREAK_RE = re.compile(r"</?(?:div|p|br|tr|li|table|h\d|section|article)[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SECTION_PATTERNS: dict[SourceType, dict[str, tuple[re.Pattern[str], ...]]] = {
    SourceType.filing_business: {
        "10-K": (
            re.compile(
                r"item\s+1\b[^a-z0-9]{0,200}business\b(.*?)(?=item\s+1a\b|item\s+2\b)",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
    },
    SourceType.filing_risk: {
        "10-K": (
            re.compile(
                r"item\s+1a\b[^a-z0-9]{0,200}risk\s+factors\b(.*?)(?=item\s+1b\b|item\s+2\b|item\s+7\b)",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
        "10-Q": (
            re.compile(
                r"item\s+1a\b[^a-z0-9]{0,200}risk\s+factors\b(.*?)(?=item\s+2\b)",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
    },
    SourceType.filing_mda: {
        "10-K": (
            re.compile(
                r"item\s+7\b[^a-z0-9]{0,200}management.?s\s+discussion\s+and\s+analysis"
                r"(?:\s+of\s+financial\s+condition\s+and\s+results\s+of\s+operations)?"
                r"(.*?)(?=item\s+7a\b|item\s+8\b)",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
        "10-Q": (
            re.compile(
                r"item\s+2\b[^a-z0-9]{0,200}management.?s\s+discussion\s+and\s+analysis"
                r"(?:\s+of\s+financial\s+condition\s+and\s+results\s+of\s+operations)?"
                r"(.*?)(?=item\s+3\b|item\s+4\b)",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
    },
    SourceType.earnings_release: {
        "8-K": (
            re.compile(
                r"item\s+2\.?02\b[^a-z0-9]{0,40}results\s+of\s+operations\s+and\s+financial\s+condition"
                r"(.*?)(?=item\s+\d|\Z)",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"results\s+of\s+operations\s+and\s+financial\s+condition(.*?)(?=item\s+\d|\Z)",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
        "6-K": (
            re.compile(
                r"earnings\s+release(.*?)(?=signature|exhibit|\Z)",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
    },
}


@dataclass(frozen=True)
class FilingMetadata:
    form_type: str
    accession_number: str
    primary_document: str
    filing_date: datetime
    report_date: date | None

    @property
    def canonical_form(self) -> str:
        return self.form_type.replace("/A", "")


@dataclass(frozen=True)
class CompanyInfo:
    cik: int
    name: str


@dataclass(frozen=True)
class SectionExtraction:
    source_type: SourceType
    documents: list[RawDocument]
    status: str
    detail: str


def _normalize_html(raw: str) -> str:
    text = _HTML_BREAK_RE.sub("\n", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


class SECEdgarIngestor:
    """Fetch the latest filing sections needed by the fundamental evidence pack."""

    def __init__(self, user_agent: str) -> None:
        self._user_agent = user_agent
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent

    def fetch(self, ticker: str) -> IngestionResult:
        normalized_ticker = ticker.upper()
        try:
            company = self._resolve_company(normalized_ticker)
            filings = self._load_recent_filings(company.cik)
        except Exception as exc:
            logger.warning("EDGAR metadata fetch failed for %s: %s", normalized_ticker, exc)
            return IngestionResult(
                documents=[],
                coverage=[
                    self._error_note(normalized_ticker, SourceType.filing_business, exc),
                    self._error_note(normalized_ticker, SourceType.filing_risk, exc),
                    self._error_note(normalized_ticker, SourceType.filing_mda, exc),
                    self._error_note(normalized_ticker, SourceType.earnings_release, exc),
                ],
            )

        extracted_sections = [
            self._extract_sections_for_filing(
                ticker=normalized_ticker,
                company_name=company.name,
                cik=company.cik,
                filing=self._latest_filing(filings, ("10-K", "10-K/A")),
                section_types=(SourceType.filing_business, SourceType.filing_risk, SourceType.filing_mda),
            ),
            self._extract_sections_for_filing(
                ticker=normalized_ticker,
                company_name=company.name,
                cik=company.cik,
                filing=self._latest_filing(filings, ("10-Q", "10-Q/A")),
                section_types=(SourceType.filing_risk, SourceType.filing_mda),
            ),
            self._extract_sections_for_filing(
                ticker=normalized_ticker,
                company_name=company.name,
                cik=company.cik,
                filing=self._latest_filing(filings, ("8-K", "8-K/A", "6-K")),
                section_types=(SourceType.earnings_release,),
            ),
        ]

        documents = [document for batch in extracted_sections for extraction in batch for document in extraction.documents]
        coverage = self._build_coverage(normalized_ticker, extracted_sections)
        return IngestionResult(documents=documents, coverage=coverage)

    def _resolve_company(self, ticker: str) -> CompanyInfo:
        response = self._session.get(EDGAR_TICKERS_URL, timeout=10)
        response.raise_for_status()
        for entry in response.json().values():
            if entry.get("ticker", "").upper() == ticker:
                return CompanyInfo(cik=int(entry["cik_str"]), name=entry.get("title", ticker))
        raise ValueError(f"CIK not found for ticker {ticker}")

    def _load_recent_filings(self, cik: int) -> dict:
        response = self._session.get(EDGAR_SUBMISSIONS_URL.format(cik=cik), timeout=10)
        response.raise_for_status()
        return response.json().get("filings", {}).get("recent", {})

    def _latest_filing(self, filings: dict, forms: tuple[str, ...]) -> FilingMetadata | None:
        all_forms = filings.get("form", [])
        accessions = filings.get("accessionNumber", [])
        documents = filings.get("primaryDocument", [])
        filing_dates = filings.get("filingDate", [])
        report_dates = filings.get("reportDate", [])
        for form, accession, document, filing_date, report_date in zip(
            all_forms,
            accessions,
            documents,
            filing_dates,
            report_dates,
        ):
            if form in forms:
                return FilingMetadata(
                    form_type=form,
                    accession_number=accession,
                    primary_document=document or "index.htm",
                    filing_date=datetime.fromisoformat(filing_date).replace(tzinfo=timezone.utc),
                    report_date=_parse_date(report_date),
                )
        return None

    def _extract_sections_for_filing(
        self,
        ticker: str,
        company_name: str,
        cik: int,
        filing: FilingMetadata | None,
        section_types: tuple[SourceType, ...],
    ) -> list[SectionExtraction]:
        if filing is None:
            return [
                SectionExtraction(
                    source_type=source_type,
                    documents=[],
                    status="missing",
                    detail="filing_not_available",
                )
                for source_type in section_types
            ]

        try:
            filing_html = self._fetch_filing_html(cik, filing)
        except Exception as exc:
            logger.warning("EDGAR filing fetch failed for %s %s: %s", ticker, filing.accession_number, exc)
            return [
                SectionExtraction(
                    source_type=source_type,
                    documents=[],
                    status="provider_error",
                    detail=str(exc),
                )
                for source_type in section_types
            ]

        normalized_text = _normalize_html(filing_html)
        return [
            self._extract_section(
                ticker=ticker,
                company_name=company_name,
                cik=cik,
                filing=filing,
                normalized_text=normalized_text,
                source_type=source_type,
            )
            for source_type in section_types
        ]

    def _fetch_filing_html(self, cik: int, filing: FilingMetadata) -> str:
        accession = filing.accession_number.replace("-", "")
        filing_url = EDGAR_FILING_URL.format(
            cik=cik,
            accession=accession,
            document=filing.primary_document,
        )
        response = self._session.get(filing_url, timeout=30)
        response.raise_for_status()
        return response.text

    def _extract_section(
        self,
        ticker: str,
        company_name: str,
        cik: int,
        filing: FilingMetadata,
        normalized_text: str,
        source_type: SourceType,
    ) -> SectionExtraction:
        patterns = _SECTION_PATTERNS.get(source_type, {}).get(filing.canonical_form, ())
        for pattern in patterns:
            match = pattern.search(normalized_text)
            if not match:
                continue
            body = re.sub(r"\s{2,}", " ", match.group(1)).strip()
            if not body:
                continue
            document = RawDocument(
                ticker=ticker,
                source_type=source_type,
                document_id=f"sec:{filing.accession_number}",
                source_url=self._filing_url(cik, filing),
                publisher=company_name,
                title=self._document_title(ticker, filing, source_type),
                text=body,
                source_quality_tier=SourceQualityTier.primary_regulatory,
                published_at=filing.filing_date,
                form_type=filing.form_type,
                accession_number=filing.accession_number,
                period_end=filing.report_date,
            )
            return SectionExtraction(
                source_type=source_type,
                documents=[document],
                status="available",
                detail=f"section_extracted:{filing.form_type}",
            )

        return SectionExtraction(
            source_type=source_type,
            documents=[],
            status="extraction_failed",
            detail=f"section_not_found:{filing.form_type}",
        )

    def _build_coverage(
        self,
        ticker: str,
        extracted_sections: Iterable[list[SectionExtraction]],
    ) -> list[SourceCoverage]:
        grouped: dict[SourceType, list[SectionExtraction]] = {
            SourceType.filing_business: [],
            SourceType.filing_risk: [],
            SourceType.filing_mda: [],
            SourceType.earnings_release: [],
        }
        for batch in extracted_sections:
            for extraction in batch:
                grouped[extraction.source_type].append(extraction)

        coverage: list[SourceCoverage] = []
        for source_type, extractions in grouped.items():
            documents = [document for extraction in extractions for document in extraction.documents]
            if documents:
                detail = ",".join(extraction.detail for extraction in extractions if extraction.documents)
                status = "available"
            else:
                status = self._worst_status(extractions)
                detail = ",".join(extraction.detail for extraction in extractions) or "missing"
            coverage.append(
                SourceCoverage(
                    ticker=ticker,
                    source_type=source_type,
                    source_quality_tier=SourceQualityTier.primary_regulatory,
                    status=status,
                    detail=detail,
                    document_count=len(documents),
                    latest_published_at=max(
                        (document.published_at for document in documents if document.published_at),
                        default=None,
                    ),
                )
            )
        return coverage

    def _worst_status(self, extractions: list[SectionExtraction]) -> str:
        if any(extraction.status == "provider_error" for extraction in extractions):
            return "provider_error"
        if any(extraction.status == "extraction_failed" for extraction in extractions):
            return "extraction_failed"
        return "missing"

    def _document_title(self, ticker: str, filing: FilingMetadata, source_type: SourceType) -> str:
        title_suffix = {
            SourceType.filing_business: "Business",
            SourceType.filing_risk: "Risk Factors",
            SourceType.filing_mda: "MD&A",
            SourceType.earnings_release: "Earnings Release",
        }[source_type]
        return f"{ticker} {filing.form_type} {title_suffix}"

    def _filing_url(self, cik: int, filing: FilingMetadata) -> str:
        accession = filing.accession_number.replace("-", "")
        return EDGAR_FILING_URL.format(
            cik=cik,
            accession=accession,
            document=filing.primary_document,
        )

    def _error_note(self, ticker: str, source_type: SourceType, error: Exception) -> SourceCoverage:
        return SourceCoverage(
            ticker=ticker,
            source_type=source_type,
            source_quality_tier=SourceQualityTier.primary_regulatory,
            status="provider_error",
            detail=str(error),
        )
