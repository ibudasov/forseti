from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models import SourceType
from app.rag.ingestion.edgar import SECEdgarIngestor

_FIXTURE_DIR = Path("/app/tests/fixtures/rag")


def _load_fixture(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text()


def _recent_filings() -> dict:
    return {
        "form": ["10-Q", "10-K", "8-K"],
        "accessionNumber": ["0002-10q", "0001-10k", "0003-8k"],
        "primaryDocument": ["10q.htm", "10k.htm", "8k.htm"],
        "filingDate": ["2026-08-01", "2026-02-01", "2026-08-15"],
        "reportDate": ["2026-06-30", "2025-12-31", "2026-08-15"],
    }


@pytest.mark.parametrize(
    ("annual_fixture", "quarterly_fixture"),
    [
        ("edgar_10k_simple.html", "edgar_10q_simple.html"),
        ("edgar_10k_nested.html", "edgar_10q_nested.html"),
    ],
)
def test_fetch_extracts_sections_from_multiple_filing_layouts(monkeypatch, annual_fixture, quarterly_fixture):
    ingestor = SECEdgarIngestor("Forseti/test")
    monkeypatch.setattr(ingestor, "_resolve_company", lambda ticker: type("Company", (), {"cik": 1, "name": "Example Corp"})())
    monkeypatch.setattr(ingestor, "_load_recent_filings", lambda cik: _recent_filings())

    fixtures = {
        "0001-10k": _load_fixture(annual_fixture),
        "0002-10q": _load_fixture(quarterly_fixture),
        "0003-8k": _load_fixture("edgar_8k_results.html"),
    }
    monkeypatch.setattr(
        ingestor,
        "_fetch_filing_html",
        lambda cik, filing: fixtures[filing.accession_number],
    )

    result = ingestor.fetch("NVDA")

    extracted_types = [document.source_type for document in result.documents]
    assert extracted_types == [
        SourceType.filing_business,
        SourceType.filing_risk,
        SourceType.filing_mda,
        SourceType.filing_risk,
        SourceType.filing_mda,
        SourceType.earnings_release,
    ]
    assert all(document.document_id.startswith("sec:") for document in result.documents)
    assert all(document.publisher == "Example Corp" for document in result.documents)
    assert all(document.published_at is not None for document in result.documents)
    assert result.coverage[0].status == "available"
    assert result.coverage[1].document_count == 2


def test_fetch_reports_extraction_failure_when_required_section_is_missing(monkeypatch):
    ingestor = SECEdgarIngestor("Forseti/test")
    monkeypatch.setattr(ingestor, "_resolve_company", lambda ticker: type("Company", (), {"cik": 1, "name": "Example Corp"})())
    monkeypatch.setattr(
        ingestor,
        "_load_recent_filings",
        lambda cik: {
            "form": ["10-Q"],
            "accessionNumber": ["0002-10q"],
            "primaryDocument": ["10q.htm"],
            "filingDate": ["2026-08-01"],
            "reportDate": ["2026-06-30"],
        },
    )
    monkeypatch.setattr(
        ingestor,
        "_fetch_filing_html",
        lambda cik, filing: "<html><body><div>Item 4. Controls and Procedures</div></body></html>",
    )

    result = ingestor.fetch("NVDA")

    statuses = {coverage.source_type: coverage.status for coverage in result.coverage}
    assert statuses[SourceType.filing_business] == "missing"
    assert statuses[SourceType.filing_risk] == "extraction_failed"
    assert statuses[SourceType.filing_mda] == "extraction_failed"
    assert statuses[SourceType.earnings_release] == "missing"
