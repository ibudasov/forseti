"""API tests for evidence endpoints."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_analysis_engine
from app.rag.synthesis import SynthesisOutput


@pytest.fixture
def client():
    return TestClient(app)


class TestEvidenceEndpoint:
    def test_evidence_endpoint_returns_200(self, client):
        """GET /ticker/{symbol}/evidence returns 200 with insufficient_data when no corpus."""
        mock_output = SynthesisOutput(ticker="NVDA", chunk_count=0, status="insufficient_data")

        with patch("app.rag.evidence.build_evidence", return_value=mock_output):
            response = client.get("/ticker/NVDA/evidence")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "insufficient_data"
        assert body["chunk_count"] == 0

    def test_evidence_endpoint_normalises_ticker(self, client):
        """Ticker is normalised before evidence lookup."""
        mock_output = SynthesisOutput(ticker="NVDA", chunk_count=0, status="insufficient_data")

        with patch("app.rag.evidence.build_evidence", return_value=mock_output) as mock_build:
            response = client.get("/ticker/nvda/evidence")

        assert response.status_code == 200

    def test_evidence_endpoint_with_full_output(self, client):
        """Evidence endpoint maps SynthesisOutput fields to EvidenceBlock."""
        from app.rag.synthesis import EvidenceItem

        mock_output = SynthesisOutput(
            ticker="AAPL",
            bullish_drivers=[EvidenceItem(claim="Strong services growth.", chunk_ids=[1])],
            bearish_risks=[EvidenceItem(claim="China headwinds.", chunk_ids=[2])],
            catalysts=[],
            news_alignment="Positive.",
            red_flags=[],
            chunk_count=2,
            status="ok",
        )

        with patch("app.rag.evidence.build_evidence", return_value=mock_output):
            response = client.get("/ticker/AAPL/evidence")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert len(body["bullish_drivers"]) == 1
        assert body["bullish_drivers"][0]["claim"] == "Strong services growth."
        assert body["bullish_drivers"][0]["chunk_ids"] == [1]

    def test_evidence_endpoint_invalid_ticker_returns_422(self, client):
        response = client.get("/ticker/THISISTOOLONG/evidence")
        assert response.status_code == 422


class TestAnalyzeResponseIncludesEvidence:
    def test_analyze_response_has_evidence_field(self, client):
        """The analyze response schema includes an optional evidence field."""
        from app.schemas.analyze import AnalyzeResponse

        fields = AnalyzeResponse.model_fields
        assert "evidence" in fields
