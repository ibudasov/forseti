"""Tests for agents.config: config loading and PIPELINE_MODE flag switching."""
from __future__ import annotations

import pytest

from agents.config import AGENTIC_PIPELINE_MODE, LINEAR_PIPELINE_MODE, load_agent_config
from app.settings import Settings


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_load_agent_config_defaults_to_linear_mode():
    config = load_agent_config(_settings())
    assert config.pipeline_mode == LINEAR_PIPELINE_MODE
    assert config.is_agentic() is False


def test_load_agent_config_switches_to_agentic_mode():
    config = load_agent_config(_settings(PIPELINE_MODE="agentic"))
    assert config.pipeline_mode == AGENTIC_PIPELINE_MODE
    assert config.is_agentic() is True


def test_load_agent_config_is_case_insensitive():
    config = load_agent_config(_settings(PIPELINE_MODE="AGENTIC"))
    assert config.pipeline_mode == AGENTIC_PIPELINE_MODE


def test_load_agent_config_rejects_unknown_mode():
    with pytest.raises(ValueError):
        load_agent_config(_settings(PIPELINE_MODE="parallel"))


def test_load_agent_config_reads_model_and_tuning_fields():
    config = load_agent_config(
        _settings(
            GEMINI_MODEL="gemini-test-model",
            AGENT_MODEL_TEMPERATURE=0.7,
            AGENT_TIMEOUT_SECONDS=45.0,
            AGENT_MAX_RETRIES=3,
        )
    )
    assert config.model_name == "gemini-test-model"
    assert config.temperature == 0.7
    assert config.timeout_seconds == 45.0
    assert config.max_retries == 3
