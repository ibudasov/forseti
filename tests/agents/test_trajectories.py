"""Offline trajectory tests for the public agent workflow entry point."""
from __future__ import annotations

import pytest

import agents.orchestration.workflow as workflow_module
from agents.config import load_agent_config
from agents.orchestration.workflow import (
    AgenticAnalysisWorkflow,
    DecisionSynthesis,
    GoogleWorkflowError,
    enforce_downgrade_only,
    validate_risk_output,
)
from app.schemas.analyze import AnalyzeResponse
from app.settings import Settings
from tests.agents.support.scripted_runner import (
    Turn,
    empty_runner,
    exploding_runner,
    scripted_runner,
)
from tests.agents.support.trace_assertions import (
    assert_never_upgraded,
    assert_retries,
    assert_step_order,
    assert_steps,
    assert_tool_calls,
)


def _deterministic_response() -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision="watchlist",
        confidence=0.4,
        reasons=["deterministic"],
        warnings=[],
        engine_version="test",
        trace_id="linear",
        entry_range=(100.0, 101.0),
        stop_loss=95.0,
        take_profit=(110.0, 115.0),
        risk_reward=2.0,
        position_size_eur=500.0,
    )


def _workflow(monkeypatch, runner):
    response = _deterministic_response()
    monkeypatch.setattr(workflow_module, "analyze_request", lambda request, engine=None: response.model_copy(deep=True))
    monkeypatch.setattr(workflow_module, "build_agent_registry", lambda config, engine=None: None)
    monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_persist_trace", lambda self, trace: None)
    return AgenticAnalysisWorkflow(
        load_agent_config(Settings(_env_file=None)),
        runner_factory=runner,
    )


def _run(monkeypatch, turns):
    return _workflow(monkeypatch, scripted_runner(turns)).analyze("NVDA")


def test_happy_path_records_the_complete_scripted_trajectory(monkeypatch):
    turns = [
        Turn("trade_analyst_supervisor", ("structured_data_collector", "transfer_to_agent")),
        Turn("fundamental_analyst", text="bullish"),
        Turn("technical_analyst", text="constructive"),
        Turn("decision_synthesizer", ("calculate_risk",)),
        Turn("critic_guardrail", text="clear"),
    ]
    response = _run(monkeypatch, turns)

    assert_steps(response.trace, [(turn.author, "completed") for turn in turns])
    assert response.trace.entered_agent_layer is True
    assert response.trace.adk_event_count == len(turns)
    assert response.trace.observed_agents == [turn.author for turn in turns]
    assert_tool_calls(response.trace, "trade_analyst_supervisor", ["structured_data_collector", "transfer_to_agent"])


def test_critic_is_ordered_after_synthesizer(monkeypatch):
    response = _run(monkeypatch, [Turn("decision_synthesizer"), Turn("critic_guardrail")])
    assert_step_order(response.trace, ["decision_synthesizer", "critic_guardrail"])


def test_critic_not_reached_is_visible(monkeypatch):
    response = _run(monkeypatch, [Turn("decision_synthesizer")])
    assert "critic_guardrail" not in response.trace.observed_agents
    assert response.decision == "watchlist"


def test_no_agent_activity_preserves_deterministic_response(monkeypatch):
    response = _workflow(monkeypatch, empty_runner()).analyze("NVDA")
    assert response.trace.entered_agent_layer is False
    assert response.trace.adk_event_count == 0
    assert response.decision == "watchlist"


def test_attempted_upgrade_is_refused_and_recorded():
    deterministic = _deterministic_response()
    proposal = DecisionSynthesis(decision="trade", confidence=0.9)
    guarded = enforce_downgrade_only(proposal, deterministic)
    assert_never_upgraded(guarded, deterministic)


def test_fabricated_risk_number_is_refused():
    deterministic = _deterministic_response()
    proposal = DecisionSynthesis(decision="watchlist", confidence=0.4, stop_loss=1.0)
    with pytest.raises(ValueError, match="agent_output_changed_risk_value: stop_loss"):
        validate_risk_output(proposal, deterministic)


def test_contradictory_specialists_and_critic_are_all_traced(monkeypatch):
    response = _run(
        monkeypatch,
        [Turn("fundamental_analyst", text="bullish"), Turn("technical_analyst", text="bearish"),
         Turn("critic_guardrail", text="contradiction")],
    )
    assert_steps(response.trace, [
        ("fundamental_analyst", "completed"),
        ("technical_analyst", "completed"),
        ("critic_guardrail", "completed"),
    ])
    assert response.decision == "watchlist"


def test_model_error_degrades_one_step_and_later_turns_continue(monkeypatch):
    response = _run(
        monkeypatch,
        [Turn("technical_analyst", error_code="TOOL_ERROR", error_message="first line\nmore"),
         Turn("critic_guardrail")],
    )
    assert response.trace.steps[0].status == "failed"
    assert response.warnings[-1] == "agent_narration_degraded: first line"
    assert response.trace.steps[1].agent_name == "critic_guardrail"


def test_runner_failure_is_mapped_to_google_workflow_error(monkeypatch):
    with pytest.raises(GoogleWorkflowError, match="vertex unavailable"):
        _workflow(monkeypatch, exploding_runner(RuntimeError("vertex unavailable"))).analyze("NVDA")


def test_token_usage_is_aggregated_across_turns(monkeypatch):
    response = _run(
        monkeypatch,
        [Turn("fundamental_analyst", token_usage={"prompt_token_count": 2, "total_token_count": 3}),
         Turn("technical_analyst", token_usage={"prompt_token_count": 4, "total_token_count": 5}),
         Turn("critic_guardrail", token_usage={"prompt_token_count": 1, "total_token_count": 2})],
    )
    assert response.trace.token_usage == {"prompt_token_count": 7, "total_token_count": 10}


def test_retries_remain_zero_until_retry_loop_exists(monkeypatch):
    response = _run(monkeypatch, [Turn("decision_synthesizer")])
    assert_retries(response.trace, "decision_synthesizer", 0)


def test_deterministic_fields_remain_identical_for_multiple_agent_trajectories(monkeypatch):
    expected = _deterministic_response()
    for turns in ([Turn("fundamental_analyst")], [Turn("critic_guardrail")]):
        response = _run(monkeypatch, turns)
        assert response.decision == expected.decision
        assert response.entry_range == expected.entry_range
        assert response.stop_loss == expected.stop_loss
        assert response.take_profit == expected.take_profit
        assert response.risk_reward == expected.risk_reward
        assert response.position_size_eur == expected.position_size_eur
