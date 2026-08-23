from __future__ import annotations

from argparse import Namespace

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


class TestApiKeyHelpers:
    @pytest.mark.parametrize("placeholder", ["", "   ", "demo", "DEMO", "changeme", "your_api_key"])
    def test_normalize_api_key_returns_none_for_placeholders(self, placeholder):
        assert normalize_api_key(placeholder) is None

    def test_normalize_api_key_keeps_real_key_and_trims_crlf(self):
        assert normalize_api_key(None) is None
        assert normalize_api_key("  REALKEY123\r\n") == "REALKEY123"

    def test_mask_api_key_masks_middle_of_long_key(self):
        masked_key = mask_api_key("REALKEY1234")

        assert masked_key == "RE***34"
        assert "ALKEY12" not in masked_key

    def test_mask_api_key_masks_short_key_completely(self):
        assert mask_api_key("abc") == "***"


class TestPayloadValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            '{"Information": "Thank you for using Alpha Vantage! This is a premium endpoint..."}',
            '{"Note": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."}',
            '{"Error Message": "Invalid API call."}',
        ],
    )
    def test_validate_earnings_payload_raises_for_refusal_payloads(self, payload):
        with pytest.raises(EarningsSourceError):
            validate_earnings_payload(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            "[1, 2, 3]",
            '{"ok": "value"}',
            "<html><body>Service unavailable</body></html>",
            "   \n\t\n  ",
        ],
    )
    def test_validate_earnings_payload_raises_for_non_csv_payloads(self, payload):
        with pytest.raises(EarningsSourceError):
            validate_earnings_payload(payload)

    def test_validate_earnings_payload_returns_valid_csv_unchanged(self):
        payload = "symbol,reportDate\nNVDA,2026-09-01"

        assert validate_earnings_payload(payload) == payload

    def test_validate_earnings_payload_accepts_leading_blank_lines(self):
        payload = "\n\n  \nsymbol,reportDate\nNVDA,2026-09-01"

        assert validate_earnings_payload(payload) == payload


class TestFetchEarningsCalendar:
    def test_fetch_earnings_calendar_csv_sends_expected_params(self, monkeypatch):
        called = {}

        def fake_get(url, params, timeout):
            called["url"] = url
            called["params"] = params
            called["timeout"] = timeout
            return httpx.Response(
                200,
                text="symbol,reportDate\nNVDA,2026-09-01",
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr("app.ingestion.earnings.httpx.get", fake_get)

        payload = fetch_earnings_calendar_csv("REALKEY")

        assert called["params"]["apikey"] == "REALKEY"
        assert called["params"]["function"] == "EARNINGS_CALENDAR"
        assert payload == "symbol,reportDate\nNVDA,2026-09-01"

    def test_fetch_earnings_calendar_csv_raises_for_http_errors(self, monkeypatch):
        def fake_get(url, params, timeout):
            return httpx.Response(500, text="error", request=httpx.Request("GET", url))

        monkeypatch.setattr("app.ingestion.earnings.httpx.get", fake_get)

        with pytest.raises(httpx.HTTPStatusError):
            fetch_earnings_calendar_csv("REALKEY")


class TestIngestEarnings:
    def test_ingest_earnings_without_key_reports_missing_marker_without_db_touch(self, monkeypatch):
        monkeypatch.setattr("app.ingestion.earnings.get_settings", lambda: Namespace(ALPHA_VANTAGE_API_KEY=""))
        monkeypatch.setattr(
            "app.ingestion.earnings.list_active_securities",
            lambda engine=None: (_ for _ in ()).throw(AssertionError("database should not be touched")),
        )

        rows, failures = ingest_earnings()

        assert rows == 0
        assert failures == [MISSING_API_KEY_MARKER]

    def test_ingest_earnings_with_placeholder_key_reports_missing_marker_without_db_touch(self, monkeypatch):
        monkeypatch.setattr("app.ingestion.earnings.get_settings", lambda: Namespace(ALPHA_VANTAGE_API_KEY="demo"))
        monkeypatch.setattr(
            "app.ingestion.earnings.list_active_securities",
            lambda engine=None: (_ for _ in ()).throw(AssertionError("database should not be touched")),
        )

        rows, failures = ingest_earnings()

        assert rows == 0
        assert failures == [MISSING_API_KEY_MARKER]

    def test_ingest_earnings_returns_source_failure_for_premium_refusal(self, db_engine, monkeypatch):
        self._seed_security(db_engine, "NVDA")
        monkeypatch.setattr("app.ingestion.earnings.get_settings", lambda: Namespace(ALPHA_VANTAGE_API_KEY="REALKEY"))
        monkeypatch.setattr(
            "app.ingestion.earnings.fetch_earnings_calendar_csv",
            lambda _: '{"Information": "This is a premium endpoint."}',
        )

        rows, failures = ingest_earnings(engine=db_engine)

        assert rows == 0
        assert failures == [SOURCE_FAILURE_MARKER]

        with Session(db_engine) as session:
            assert session.exec(select(EarningsEvent)).all() == []

    def test_ingest_earnings_returns_source_failure_when_no_rows_match_active_tickers(self, db_engine, monkeypatch):
        self._seed_security(db_engine, "NVDA")
        monkeypatch.setattr("app.ingestion.earnings.get_settings", lambda: Namespace(ALPHA_VANTAGE_API_KEY="REALKEY"))
        monkeypatch.setattr(
            "app.ingestion.earnings.fetch_earnings_calendar_csv",
            lambda _: "symbol,reportDate\nAAPL,2026-09-01",
        )

        rows, failures = ingest_earnings(engine=db_engine)

        assert rows == 0
        assert failures == [SOURCE_FAILURE_MARKER]

    def test_ingest_earnings_persists_only_active_tickers(self, db_engine, monkeypatch):
        nvda = self._seed_security(db_engine, "NVDA")
        msft = self._seed_security(db_engine, "MSFT")
        self._seed_security(db_engine, "UNKN")
        monkeypatch.setattr("app.ingestion.earnings.get_settings", lambda: Namespace(ALPHA_VANTAGE_API_KEY="REALKEY"))
        monkeypatch.setattr(
            "app.ingestion.earnings.fetch_earnings_calendar_csv",
            lambda _: "\n".join(
                [
                    "symbol,reportDate",
                    "NVDA,2026-09-01",
                    "MSFT,2026-09-03",
                    "AAPL,2026-09-02",
                ]
            ),
        )

        rows, failures = ingest_earnings(engine=db_engine)

        assert rows == 2
        assert failures == []

        with Session(db_engine) as session:
            events = session.exec(select(EarningsEvent)).all()

        assert len(events) == 2
        assert {event.security_id for event in events} == {nvda.id, msft.id}
        assert all(event.confirmed is False for event in events)

    def test_ingest_earnings_ticker_filter_trims_and_normalizes(self, db_engine, monkeypatch):
        nvda = self._seed_security(db_engine, "NVDA")
        self._seed_security(db_engine, "MSFT")
        monkeypatch.setattr("app.ingestion.earnings.get_settings", lambda: Namespace(ALPHA_VANTAGE_API_KEY="REALKEY"))
        monkeypatch.setattr(
            "app.ingestion.earnings.fetch_earnings_calendar_csv",
            lambda _: "symbol,reportDate\nNVDA,2026-09-01\nMSFT,2026-09-03",
        )

        rows, failures = ingest_earnings(engine=db_engine, ticker=" nvda ")

        assert rows == 1
        assert failures == []

        with Session(db_engine) as session:
            events = session.exec(select(EarningsEvent)).all()

        assert len(events) == 1
        assert events[0].security_id == nvda.id

    def test_ingest_earnings_returns_source_failure_on_connect_error(self, db_engine, monkeypatch):
        self._seed_security(db_engine, "NVDA")
        monkeypatch.setattr("app.ingestion.earnings.get_settings", lambda: Namespace(ALPHA_VANTAGE_API_KEY="REALKEY"))

        def raise_connect_error(_):
            raise httpx.ConnectError("network down", request=httpx.Request("GET", "https://www.alphavantage.co/query"))

        monkeypatch.setattr("app.ingestion.earnings.fetch_earnings_calendar_csv", raise_connect_error)

        rows, failures = ingest_earnings(engine=db_engine)

        assert rows == 0
        assert failures == [SOURCE_FAILURE_MARKER]

    @staticmethod
    def _seed_security(db_engine, ticker: str) -> Security:
        security = Security(ticker=ticker, name=f"{ticker} Inc.", exchange="NASDAQ", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
        return security
