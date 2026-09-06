from agents.adk_app.agent import root_agent


def test_adk_app_exposes_trade_supervisor():
    assert root_agent.name == "trade_analyst_supervisor"
    assert {agent.name for agent in root_agent.sub_agents} == {
        "fundamental_analyst",
        "technical_analyst",
        "decision_synthesizer",
        "critic_guardrail",
    }
