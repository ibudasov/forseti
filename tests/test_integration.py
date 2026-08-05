"""High-level integration tests for Forseti API endpoints."""

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    """Provide a test client for the FastAPI application."""
    return TestClient(app)


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
