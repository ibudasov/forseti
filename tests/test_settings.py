"""Tests for app.settings: RAG_FAIL_LOUD flag parsing."""
from __future__ import annotations

from app.settings import Settings


def test_rag_fail_loud_defaults_to_false():
    settings = Settings(_env_file=None)
    assert settings.RAG_FAIL_LOUD is False


def test_edgar_user_agent_defaults_to_canonical_identity():
    settings = Settings(_env_file=None)
    assert settings.EDGAR_USER_AGENT == "Forseti/0.1 (gazer-flair9o@icloud.com)"


def test_rag_fail_loud_env_var_parses_to_true(monkeypatch):
    monkeypatch.setenv("RAG_FAIL_LOUD", "true")
    settings = Settings(_env_file=None)
    assert settings.RAG_FAIL_LOUD is True
