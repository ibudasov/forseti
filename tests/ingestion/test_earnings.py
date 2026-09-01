from __future__ import annotations

import httpx
import pytest
from sqlmodel import Session, select

from app.db.models import EarningsEvent, Security
from app.ingestion.earnings import (
    MISSING_API_KEY_MARKER,
    SOURCE_FAILURE_MARKER,
    EarningsSourceError,
    fetch_earnings_calendar_csv,
    ingest_earnings,
    mask_api_key,
    normalize_api_key,
    parse_earnings_calendar,
    validate_earnings_payload,
)


class TestEarningsParsing:
    def test_parse_earnings_calendar_filters_active_tickers_and_sets_unconfirmed(self):
        csv_payload = "\n".join(
            [
                "symbol,name,reportDate,fiscalDateEnding,estimate,currency",
                "NVDA,NVIDIA Corporation,2026-09-01,2026-07-31,0.85,USD",
                "MSFT,Microsoft Corporation,2026-09-03,2026-06-30,2.25,USD",
                "UNKN,Unknown Co,2026-09-10,2026-06-30,1.00,USD",
            ]
        )

        events = parse_earnings_calendar(csv_payload, {"NVDA": 1, "MSFT": 2})

        assert len(events) == 2
        assert {event.security_id for event in events} == {1, 2}
        assert all(event.confirmed is False for event in events)


class TestEarningsApiKey:
    def test_normalize_api_key_returns_none_for_missing_key(self):
        assert normalize_api_key(None) is None

    def test_normalize_api_key_removes_whitespace_and_crlf(self):
        assert normalize_api_key("  REALKEY123\r\n") == "REALKEY123"

    @pytest.mark.parametrize("api_key", ["", "   ", "demo", "DEMO", "changeme", "your_api_key"])
    def test_normalize_api_key_rejects_placeholder_values(self, api_key):
        assert normalize_api_key(api_key) is None

    def test_mask_api_key_masks_the_middle_of_long_keys(self):
        masked_api_key = mask_api_key("REALKEY123")

        assert masked_api_key == "RE***23"
        assert "ALKEY1" not in masked_api_key

    def test_mask_api_key_masks_short_keys_completely(self):
        assert mask_api_key("key") == "***"


class TestEarningsPayloadValidation:
    def test_validate_earnings_payload_returns_valid_csv_unchanged(self):
        payload = "symbol,reportDate\nNVDA,2026-09-01\n"

        assert validate_earnings_payload(payload) == payload

    @pytest.mark.parametrize(
        "payload",
        [
            '{"Information": "This is a premium endpoint"}',
            '{"Note": "Thank you for using Alpha Vantage"}',
            '{"Error Message": "Invalid API call"}',
        ],
    )
    def test_validate_earnings_payload_rejects_refusal_messages(self, payload):
        with pytest.raises(EarningsSourceError):
            validate_earnings_payload(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            "[1, 2, 3]",
            '{"status": "unavailable"}',
            "<html>error</html>",
            "   \n\t",
        ],
    )
    def test_validate_earnings_payload_rejects_invalid_content(self, payload):
        with pytest.raises(EarningsSourceError):
            validate_earnings_payload(payload)

    def test_validate_earnings_payload_allows_leading_blank_lines(self):
        payload = "\n \nsymbol,reportDate\nNVDA,2026-09-01\n"

        assert validate_earnings_payload(payload) == payload


class TestEarningsFetch:
    def test_fetch_earnings_calendar_csv_sends_api_key_and_function(self, monkeypatch):
        request = httpx.Request("GET", "https://www.alphavantage.co/query")

        def get_stub(url, params, timeout):
            assert url == "https://www.alphavantage.co/query"
            assert params["apikey"] == "REALKEY123"
            assert params["function"] == "EARNINGS_CALENDAR"
            assert timeout == 30.0
            return httpx.Response(200, text="symbol,reportDate", request=request)

        monkeypatch.setattr("app.ingestion.earnings.httpx.get", get_stub)

        assert fetch_earnings_calendar_csv("REALKEY123") == "symbol,reportDate"

    def test_fetch_earnings_calendar_csv_raises_for_http_errors(self, monkeypatch):
        request = httpx.Request("GET", "https://www.alphavantage.co/query")
        monkeypatch.setattr(
            "app.ingestion.earnings.httpx.get",
            lambda *args, **kwargs: httpx.Response(500, request=request),
        )

        with pytest.raises(httpx.HTTPStatusError):
            fetch_earnings_calendar_csv("REALKEY123")


class TestEarningsIngestion:
    def test_ingest_earnings_reports_missing_key_without_database_access(self, monkeypatch):
        self._stub_settings(monkeypatch, "")
        monkeypatch.setattr(
            "app.ingestion.earnings.list_active_securities",
            lambda **kwargs: pytest.fail("database should not be accessed"),
        )

        assert ingest_earnings() == (0, [MISSING_API_KEY_MARKER])

    def test_ingest_earnings_reports_placeholder_key_without_database_access(self, monkeypatch):
        self._stub_settings(monkeypatch, "demo")
        monkeypatch.setattr(
            "app.ingestion.earnings.list_active_securities",
            lambda **kwargs: pytest.fail("database should not be accessed"),
        )

        assert ingest_earnings() == (0, [MISSING_API_KEY_MARKER])

    def test_ingest_earnings_reports_premium_refusal_without_persisting(self, db_engine, monkeypatch):
        self._stub_settings(monkeypatch, "REALKEY123")
        self._seed_securities(db_engine, "NVDA")
        monkeypatch.setattr(
            "app.ingestion.earnings.fetch_earnings_calendar_csv",
            lambda _: '{"Information": "This is a premium endpoint"}',
        )

        assert ingest_earnings(engine=db_engine) == (0, [SOURCE_FAILURE_MARKER])
        assert self._stored_events(db_engine) == []

    def test_ingest_earnings_reports_no_matching_active_tickers(self, db_engine, monkeypatch):
        self._stub_settings(monkeypatch, "REALKEY123")
        self._seed_securities(db_engine, "NVDA")
        monkeypatch.setattr(
            "app.ingestion.earnings.fetch_earnings_calendar_csv",
            lambda _: "symbol,reportDate\nMSFT,2026-09-03\n",
        )

        assert ingest_earnings(engine=db_engine) == (0, [SOURCE_FAILURE_MARKER])

    def test_ingest_earnings_persists_matching_active_tickers(self, db_engine, monkeypatch):
        self._stub_settings(monkeypatch, "REALKEY123")
        self._seed_securities(db_engine, "NVDA", "MSFT")
        monkeypatch.setattr(
            "app.ingestion.earnings.fetch_earnings_calendar_csv",
            lambda _: "symbol,reportDate\nNVDA,2026-09-01\nMSFT,2026-09-03\nUNKN,2026-09-10\n",
        )

        assert ingest_earnings(engine=db_engine) == (2, [])
        events = self._stored_events(db_engine)
        assert len(events) == 2
        assert all(event.confirmed is False for event in events)

    def test_ingest_earnings_filters_to_requested_ticker(self, db_engine, monkeypatch):
        self._stub_settings(monkeypatch, "REALKEY123")
        self._seed_securities(db_engine, "NVDA", "MSFT")
        monkeypatch.setattr(
            "app.ingestion.earnings.fetch_earnings_calendar_csv",
            lambda _: "symbol,reportDate\nNVDA,2026-09-01\nMSFT,2026-09-03\n",
        )

        assert ingest_earnings(engine=db_engine, ticker=" nvda ") == (1, [])
        assert len(self._stored_events(db_engine)) == 1

    def test_ingest_earnings_reports_transport_errors(self, db_engine, monkeypatch):
        self._stub_settings(monkeypatch, "REALKEY123")
        self._seed_securities(db_engine, "NVDA")
        monkeypatch.setattr(
            "app.ingestion.earnings.fetch_earnings_calendar_csv",
            lambda _: (_ for _ in ()).throw(httpx.ConnectError("unavailable")),
        )

        assert ingest_earnings(engine=db_engine) == (0, [SOURCE_FAILURE_MARKER])

    @staticmethod
    def _stub_settings(monkeypatch, api_key):
        class SettingsStub:
            ALPHA_VANTAGE_API_KEY = api_key

        monkeypatch.setattr("app.ingestion.earnings.get_settings", SettingsStub)

    @staticmethod
    def _seed_securities(db_engine, *tickers):
        with Session(db_engine) as session:
            for ticker in tickers:
                session.add(
                    Security(ticker=ticker, name=f"{ticker} Corporation", exchange="NASDAQ", sector_tag="ai")
                )
            session.commit()

    @staticmethod
    def _stored_events(db_engine):
        with Session(db_engine) as session:
            return session.exec(select(EarningsEvent)).all()
