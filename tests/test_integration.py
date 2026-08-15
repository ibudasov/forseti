"""High-level integration tests for Forseti API endpoints."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import PriceBar, Recommendation, Security
from app.main import app
from app.main import get_analysis_engine
from tests.test_db import db_engine


@pytest.fixture
def client():
    """Provide a test client for the FastAPI application."""
    return TestClient(app)


@pytest.fixture
def db_client(db_engine):
    app.dependency_overrides[get_analysis_engine] = lambda: db_engine
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestRootEndpoint:
    """Integration tests for the root endpoint."""

    def test_read_root_returns_welcome_message(self, client):
        """Test that GET / returns a welcome message with 200 status."""
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Welcome to Forseti API!"}

    def test_read_root_response_content_type(self, client):
        """Test that GET / returns JSON content type."""
        response = client.get("/")
        assert response.headers["content-type"] == "application/json"

    def test_read_root_has_message_key(self, client):
        """Test that GET / response contains the 'message' key."""
        response = client.get("/")
        data = response.json()
        assert "message" in data
        assert isinstance(data["message"], str)
        assert len(data["message"]) > 0


class TestAnalyzeEndpoint:
    def test_post_analyze_happy_path_persists_recommendation(self, db_client, db_engine):
        security = Security(ticker="NVDA", name="NVIDIA Corporation", exchange="NASDAQ", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            session.add_all(
                [
                    PriceBar(
                        security_id=security.id,
                        bar_date="2026-01-01",
                        open="100.0000",
                        high="103.0000",
                        low="99.0000",
                        close="100.0000",
                        volume=1_000_000,
                    ),
                    PriceBar(
                        security_id=security.id,
                        bar_date="2026-01-02",
                        open="101.0000",
                        high="104.0000",
                        low="100.0000",
                        close="102.5000",
                        volume=1_100_000,
                    ),
                ]
            )
            session.commit()

        response = db_client.post(
            "/analyze",
            json={
                "ticker": " nvda ",
                "account_size_eur": 10000,
                "risk_percentage": 0.01,
                "max_position_size_eur": 500,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "NVDA"
        assert body["decision"] in ("trade", "watchlist", "no_trade")
        assert body["engine_version"] == "v1.rules.0"
        assert body["trace_id"]
        assert "confidence" in body
        assert "reasons" in body
        assert "warnings" in body

    def test_post_analyze_returns_422_for_empty_ticker(self, db_client):
        response = db_client.post("/analyze", json={"ticker": "   "})
        assert response.status_code == 422

    def test_post_analyze_returns_422_for_url_like_ticker(self, db_client):
        response = db_client.post("/analyze", json={"ticker": "https://broker.example/NVDA"})
        assert response.status_code == 422

    def test_post_analyze_response_shape_for_watchlist(self, db_client, db_engine):
        security = Security(ticker="AMD", name="Advanced Micro Devices", exchange="NASDAQ", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            session.add(
                PriceBar(
                    security_id=security.id,
                    bar_date="2026-02-01",
                    open="95.0000",
                    high="96.0000",
                    low="94.0000",
                    close="95.2000",
                    volume=900_000,
                )
            )
            session.commit()

        response = db_client.post("/analyze", json={"ticker": "AMD"})
        assert response.status_code == 200
        body = response.json()
        for field in (
            "ticker",
            "decision",
            "entry_range",
            "stop_loss",
            "take_profit",
            "risk_reward",
            "position_size_eur",
            "confidence",
            "reasons",
            "warnings",
            "engine_version",
            "created_at",
            "trace_id",
        ):
            assert field in body
        assert body["decision"] == "watchlist"
        assert body["warnings"] == ["insufficient_price_data"]


class TestScreeningEndpoint:
    def test_get_screening_returns_summary_and_priority_order(self, db_client, db_engine, monkeypatch):
        from app.db.models import Security
        from app.schemas.analyze import AnalyzeResponse

        with Session(db_engine) as session:
            session.add_all(
                [
                    Security(ticker="MSFT", name="Microsoft", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="AMD", name="AMD", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="AAPL", name="Apple", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="QQQ", name="Nasdaq", exchange="NASDAQ", sector_tag="ai", is_active=False),
                ]
            )
            session.commit()

        def fake_analyze(symbol, engine=None, today=None):
            mapping = {
                "MSFT": AnalyzeResponse(
                    ticker="MSFT",
                    decision="trade",
                    confidence=0.9,
                    reasons=["screened trade"],
                    warnings=[],
                    engine_version="v1.rules.0",
                    trace_id="",
                ),
                "AMD": AnalyzeResponse(
                    ticker="AMD",
                    decision="watchlist",
                    confidence=0.5,
                    reasons=["screened watchlist"],
                    warnings=["insufficient_price_data"],
                    engine_version="v1.rules.0",
                    trace_id="",
                ),
                "AAPL": AnalyzeResponse(
                    ticker="AAPL",
                    decision="no_trade",
                    confidence=0.2,
                    reasons=["screened no_trade"],
                    warnings=["no_price_data"],
                    engine_version="v1.rules.0",
                    trace_id="",
                ),
            }
            return mapping[symbol]

        monkeypatch.setattr("app.services.analyzer.analyze", fake_analyze)

        response = db_client.get("/screening")

        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) >= {"summary", "items"}
        assert body["summary"] == {"total": 3, "trade": 1, "watchlist": 1, "no_trade": 1, "errors": 0}
        assert [item["ticker"] for item in body["items"]] == ["MSFT", "AMD", "AAPL"]
        assert [item["decision"] for item in body["items"]] == ["trade", "watchlist", "no_trade"]


class TestTickerEndpoint:
    def test_get_ticker_happy_path_full_data(self, db_client, db_engine):
        from decimal import Decimal
        from datetime import date, timedelta
        from app.db.models import EarningsEvent, Fundamental, TechnicalFeature

        security = Security(ticker="NVDA", name="NVIDIA Corporation", exchange="NASDAQ", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            today = date.today()
            bar1_date = today - timedelta(days=1)
            bar2_date = today
            session.add_all([
                PriceBar(security_id=security.id, bar_date=bar2_date, open=Decimal("101.0"), high=Decimal("104.0"), low=Decimal("100.0"), close=Decimal("102.5"), volume=1_100_000),
                PriceBar(security_id=security.id, bar_date=bar1_date, open=Decimal("100.0"), high=Decimal("103.0"), low=Decimal("99.0"), close=Decimal("101.0"), volume=1_000_000),
                TechnicalFeature(security_id=security.id, as_of_date=bar2_date, rsi_14=Decimal("58.1234"), sma_50=Decimal("99.5"), sma_200=Decimal("88.0"), volume_trend=Decimal("1.05")),
                Fundamental(security_id=security.id, as_of_date=date(2025, 12, 31), revenue_growth=Decimal("0.62"), fcf=Decimal("21000000000.0"), debt_to_equity=Decimal("0.41"), eps_trend=Decimal("0.18"), margins=Decimal("0.55"), raw_payload={}),
                EarningsEvent(security_id=security.id, report_date=today + timedelta(days=30), confirmed=False),
            ])
            session.commit()

        response = db_client.get("/ticker/NVDA")
        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "NVDA"
        assert body["name"] == "NVIDIA Corporation"
        assert body["exchange"] == "NASDAQ"
        assert body["sector_tag"] == "ai"
        assert body["is_active"] is True
        assert body["price_bars_stored"] == 2
        assert body["latest_price_bar"]["close"] == 102.5
        assert body["latest_price_bar"]["volume"] == 1_100_000
        assert body["latest_technical_features"]["rsi_14"] == pytest.approx(58.1234, rel=1e-4)
        assert body["latest_fundamentals"]["revenue_growth"] == pytest.approx(0.62, rel=1e-4)
        assert body["next_earnings_date"] == (today + timedelta(days=30)).isoformat()
        assert body["warnings"] == []
        assert body["data_freshness"]["is_price_data_stale"] is False

    def test_get_ticker_normalizes_symbol(self, db_client, db_engine):
        security = Security(ticker="NVDX", name="NVDX Corp", exchange="NYSE", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()

        response = db_client.get("/ticker/%20nvdx%20")
        assert response.status_code == 200
        assert response.json()["ticker"] == "NVDX"

    def test_get_ticker_without_market_data(self, db_client, db_engine):
        security = Security(ticker="BARE1", name="Bare One", exchange="NYSE", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()

        response = db_client.get("/ticker/BARE1")
        assert response.status_code == 200
        body = response.json()
        assert body["latest_price_bar"] is None
        assert body["price_bars_stored"] == 0
        assert body["data_freshness"]["is_price_data_stale"] is True
        assert "no_price_data" in body["warnings"]
        assert "no_technical_features" in body["warnings"]
        assert "no_fundamentals" in body["warnings"]
        assert "no_earnings_data" in body["warnings"]

    def test_get_ticker_with_stale_price_data(self, db_client, db_engine):
        from decimal import Decimal
        from datetime import date, timedelta

        security = Security(ticker="STALE", name="Stale Corp", exchange="NYSE", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            stale_date = date.today() - timedelta(days=30)
            session.add(PriceBar(security_id=security.id, bar_date=stale_date, open=Decimal("10"), high=Decimal("11"), low=Decimal("9"), close=Decimal("10"), volume=100))
            session.commit()

        response = db_client.get("/ticker/STALE")
        assert response.status_code == 200
        body = response.json()
        assert "stale_price_data" in body["warnings"]
        assert body["data_freshness"]["price_data_age_days"] == 30

    def test_get_ticker_unknown_returns_404(self, db_client):
        response = db_client.get("/ticker/MSFT")
        assert response.status_code == 404
        assert response.json()["detail"] == "ticker_not_found: MSFT"

    def test_get_ticker_invalid_symbol_returns_422(self, db_client):
        for symbol in [
            "%20",
            "TOOLONGTICKER",
            "NV%24DA",
        ]:
            response = db_client.get(f"/ticker/{symbol}")
            assert response.status_code == 422, f"Expected 422 for {symbol}"

    def test_get_ticker_inactive_security_warns(self, db_client, db_engine):
        security = Security(ticker="DEAD1", name="Dead Corp", exchange="NYSE", sector_tag="ai", is_active=False)
        with Session(db_engine) as session:
            session.add(security)
            session.commit()

        response = db_client.get("/ticker/DEAD1")
        assert response.status_code == 200
        body = response.json()
        assert "security_inactive" in body["warnings"]
        assert body["is_active"] is False

    def test_get_ticker_response_contains_no_analysis_fields(self, db_client, db_engine):
        security = Security(ticker="CHK1X", name="Check Corp", exchange="NYSE", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()

        response = db_client.get("/ticker/CHK1X")
        assert response.status_code == 200
        body = response.json()
        for absent_field in ("engine_version", "trace_id", "decision"):
            assert absent_field not in body
