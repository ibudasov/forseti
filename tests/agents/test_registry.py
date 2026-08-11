"""Tests for agent registry construction (no network calls)."""
from __future__ import annotations

from agents.config import load_agent_config
from agents.orchestration.registry import (
    CRITIC_NAME,
    DECISION_SYNTHESIZER_NAME,
    FUNDAMENTAL_ANALYST_NAME,
    INPUT_RESOLVER_TOOL_NAME,
    RISK_MANAGER_TOOL_NAME,
    ROOT_AGENT_NAME,
    STRUCTURED_DATA_COLLECTOR_TOOL_NAME,
    TECHNICAL_ANALYST_NAME,
    build_agent_registry,
)
from app.settings import Settings


def _config():
    return load_agent_config(Settings(_env_file=None))


def test_registry_builds_deterministic_tools_without_retriever_by_default():
    registry = build_agent_registry(_config())

    assert INPUT_RESOLVER_TOOL_NAME in registry.tools
    assert STRUCTURED_DATA_COLLECTOR_TOOL_NAME in registry.tools
    assert RISK_MANAGER_TOOL_NAME in registry.tools
    assert "retriever" not in registry.tools


def test_registry_includes_retriever_when_embedding_client_provided():
    class _FakeEmbeddingClient:
        def embed_texts(self, texts):
            return [[0.0] for _ in texts]

    registry = build_agent_registry(_config(), embedding_client=_FakeEmbeddingClient())

    assert "retriever" in registry.tools


def test_registry_builds_all_specialist_agents():
    registry = build_agent_registry(_config())

    assert set(registry.specialists.keys()) == {
        FUNDAMENTAL_ANALYST_NAME,
        TECHNICAL_ANALYST_NAME,
        DECISION_SYNTHESIZER_NAME,
        CRITIC_NAME,
    }


def test_root_agent_orchestrates_tools_and_specialists():
    registry = build_agent_registry(_config())

    assert registry.root_agent is not None
    assert registry.root_agent.name == ROOT_AGENT_NAME

    sub_agent_names = {agent.name for agent in registry.root_agent.sub_agents}
    assert sub_agent_names == set(registry.specialists.keys())

    tool_names = {tool.name for tool in registry.root_agent.tools}
    assert "resolve_ticker" in tool_names


def test_specialist_instructions_state_hard_rules():
    registry = build_agent_registry(_config())

    for agent in registry.specialists.values():
        assert "never upgrade" in agent.instruction.lower()
        assert "deterministic risk engine" in agent.instruction.lower()
