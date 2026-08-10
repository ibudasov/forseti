"""SEC EDGAR ingestor — fetches Business (Item 1) and Risk Factors (Item 1A) sections."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List

import requests

from app.db.models import SourceType
from app.rag.ingestion.base import RawDocument

logger = logging.getLogger(__name__)

EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
EDGAR_FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

_ITEM1_RE = re.compile(
    r"item\s+1\b[^a-z]*?business\b(.*?)(?=item\s+1a\b|item\s+2\b)",
    re.IGNORECASE | re.DOTALL,
)
_ITEM1A_RE = re.compile(
    r"item\s+1a\b[^a-z]*?risk\s+factors\b(.*?)(?=item\s+1b\b|item\s+2\b)",
    re.IGNORECASE | re.DOTALL,
)


def _clean_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"&\w+;", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


class SECEdgarIngestor:
    """Fetches the latest 10-K Business and Risk Factors sections for a ticker."""

    def __init__(self, user_agent: str) -> None:
        self._user_agent = user_agent
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent

    def fetch(self, ticker: str) -> List[RawDocument]:
        try:
            cik = self._resolve_cik(ticker)
            return self._fetch_sections(ticker, cik)
        except Exception as exc:
            logger.warning("EDGAR fetch failed for %s: %s", ticker, exc)
            return []

    def _resolve_cik(self, ticker: str) -> int:
        resp = self._session.get(
            "https://www.sec.gov/files/company_tickers.json", timeout=10
        )
        resp.raise_for_status()
        for entry in resp.json().values():
            if entry.get("ticker", "").upper() == ticker.upper():
                return int(entry["cik_str"])
        raise ValueError(f"CIK not found for ticker {ticker}")

    def _fetch_sections(self, ticker: str, cik: int) -> List[RawDocument]:
        url = EDGAR_SUBMISSIONS_URL.format(cik=cik)
        resp = self._session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        accession_raw = self._latest_10k_accession(data)
        if not accession_raw:
            return []

        accession = accession_raw.replace("-", "")
        document = self._primary_document(data, accession_raw)
        filing_url = EDGAR_FILING_URL.format(cik=cik, accession=accession, document=document)

        resp = self._session.get(filing_url, timeout=30)
        resp.raise_for_status()
        filing_text = resp.text

        documents: List[RawDocument] = []
        published_at = self._latest_10k_date(data)

        for section_re, source_type in [
            (_ITEM1_RE, SourceType.filing_business),
            (_ITEM1A_RE, SourceType.filing_risk),
        ]:
            match = section_re.search(filing_text)
            if match:
                text = _clean_html(match.group(1))
                if text:
                    documents.append(
                        RawDocument(
                            ticker=ticker.upper(),
                            source_type=source_type,
                            source_url=filing_url,
                            text=text,
                            published_at=published_at,
                        )
                    )

        return documents

    @staticmethod
    def _latest_10k_accession(data: dict) -> str:
        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accessions = filings.get("accessionNumber", [])
        for form, accession in zip(forms, accessions):
            if form == "10-K":
                return accession
        return ""

    @staticmethod
    def _latest_10k_date(data: dict) -> datetime:
        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        for form, filing_date in zip(forms, dates):
            if form == "10-K":
                return datetime.fromisoformat(filing_date).replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    @staticmethod
    def _primary_document(data: dict, accession_raw: str) -> str:
        filings = data.get("filings", {}).get("recent", {})
        accessions = filings.get("accessionNumber", [])
        documents = filings.get("primaryDocument", [])
        for accession, document in zip(accessions, documents):
            if accession == accession_raw:
                return document
        return "index.htm"
