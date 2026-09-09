from agents.adk_app.agent import root_agent
from agents.config import load_agent_config
from agents.orchestration.registry import build_agent_registry
from app.settings import Settings


def test_adk_app_exposes_trade_supervisor():
    expected_root_agent = build_agent_registry(load_agent_config(Settings(_env_file=None))).root_agent

    assert expected_root_agent is not None
    assert root_agent.name == expected_root_agent.name
    assert {agent.name for agent in root_agent.sub_agents} == {
        "fundamental_analyst",
        "technical_analyst",
        "decision_synthesizer",
        "critic_guardrail",
    }
