from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import SourceType
from app.rag.ingestion.earnings import EarningsCallIngestor


class _Frame:
    def __init__(self, rows):
        self._rows = rows
        self.empty = not rows

    def tail(self, count: int):
        del count
        return self

    def iterrows(self):
        for index, row in enumerate(self._rows):
            yield index, row


class _Ticker:
    def __init__(self):
        self.recommendations = _Frame(
            [
                {"period": "0m", "strongBuy": 10, "buy": 5, "hold": 2, "sell": 1, "strongSell": 0},
            ]
        )
        self.calendar = {"Earnings Date": "2026-11-10", "EPS Estimate": 1.23}


def test_fetch_uses_truthful_source_types(monkeypatch):
    monkeypatch.setattr("yfinance.Ticker", lambda ticker: _Ticker())
    ingestor = EarningsCallIngestor()

    result = ingestor.fetch("NVDA")

    assert [document.source_type for document in result.documents] == [
        SourceType.analyst_recommendations,
        SourceType.earnings_calendar,
    ]
    assert result.coverage[-1].source_type == SourceType.earnings_call_transcript
    assert result.coverage[-1].status == "unavailable"


def test_fetch_transcript_when_configured(monkeypatch):
    monkeypatch.setattr("yfinance.Ticker", lambda ticker: _Ticker())

    class _Response:
        text = "<html><body><h1>Transcript</h1><p>Management discussed demand.</p></body></html>"

        def raise_for_status(self):
            return None

    transcript_now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    monkeypatch.setattr("app.rag.ingestion.earnings.datetime", type("FakeDateTime", (), {
        "now": staticmethod(lambda tz=None: transcript_now),
    }))

    ingestor = EarningsCallIngestor(transcript_url_template="https://example.com/{ticker}")
    monkeypatch.setattr(ingestor._session, "get", lambda url, timeout: _Response())

    result = ingestor.fetch("NVDA")

    transcript_document = result.documents[-1]
    assert transcript_document.source_type == SourceType.earnings_call_transcript
    assert transcript_document.source_url == "https://example.com/NVDA"
    assert "Management discussed demand." in transcript_document.text
    assert result.coverage[-1].status == "available"
