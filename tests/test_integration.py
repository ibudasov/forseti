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
        assert body["decision"] == "trade"
        assert body["engine_version"] == "v1.placeholder.0"
        assert body["trace_id"]
        assert len(body["entry_range"]) == 2
        assert len(body["take_profit"]) == 2
        assert body["stop_loss"] < body["entry_range"][0]
        assert body["risk_reward"] >= 1.5
        assert body["position_size_eur"] == 500.0

        with Session(db_engine) as session:
            persisted = session.exec(select(Recommendation)).all()
            assert len(persisted) == 1
            assert persisted[0].decision == "trade"
            assert persisted[0].engine_version == "v1.placeholder.0"
            assert persisted[0].full_payload["trace_id"] == body["trace_id"]

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
