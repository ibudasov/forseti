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


def test_pipeline_override_defaults_to_false():
    settings = Settings(_env_file=None)
    assert settings.ALLOW_PIPELINE_OVERRIDE is False


def test_fundamental_agent_mode_defaults_to_off():
    settings = Settings(_env_file=None)
    assert settings.FUNDAMENTAL_AGENT_MODE == "off"


def test_pipeline_override_env_var_parses_to_true(monkeypatch):
    monkeypatch.setenv("ALLOW_PIPELINE_OVERRIDE", "true")
    settings = Settings(_env_file=None)
    assert settings.ALLOW_PIPELINE_OVERRIDE is True


def test_llm_io_debug_capture_defaults_off():
    settings = Settings(_env_file=None)
    assert settings.DEBUG_LLM_IO is False
    assert settings.DEBUG_LLM_IO_DIR == "/tmp/forseti-llm-io"


def test_earnings_transcript_template_defaults_to_none(monkeypatch):
    monkeypatch.delenv("EARNINGS_TRANSCRIPT_URL_TEMPLATE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.EARNINGS_TRANSCRIPT_URL_TEMPLATE is None


def test_earnings_transcript_template_reads_env_var(monkeypatch):
    monkeypatch.setenv("EARNINGS_TRANSCRIPT_URL_TEMPLATE", "https://example.com/{ticker}")
    settings = Settings(_env_file=None)
    assert settings.EARNINGS_TRANSCRIPT_URL_TEMPLATE == "https://example.com/{ticker}"
