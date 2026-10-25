from __future__ import annotations

import json
from pathlib import Path

from agents.orchestration.registry import DECISION_SYNTHESIZER_NAME, SPECIALIST_ORDER, TECHNICAL_ANALYST_NAME
from app.schemas.analyze import AnalysisTrace
from app.trust.invariants import check_synthesizer_step_zero_tokens_in_typed_mode

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "trust" / "clear_trade_trajectory.json"
TYPED_TRACE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "trust" / "typed_technical_view_trajectory.json"
TYPED_VIEWS_TRACE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "trust" / "typed_views_trajectory.json"
TYPED_SYNTHESIZER_TRACE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "trust" / "typed_synthesizer_trajectory.json"
ALLOWED_TOOL_CALLS = {
    DECISION_SYNTHESIZER_NAME: {"calculate_risk"},
}


def _load_trace(fixture_path: Path = TRACE_FIXTURE) -> tuple[str, AnalysisTrace]:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    source = str(payload["source"]).strip()
    trace = AnalysisTrace.model_validate(
        {
            "run_id": payload["run_id"],
            "ticker": payload["ticker"],
            "steps": payload["steps"],
        }
    )
    return source, trace


def test_recorded_trace_fixture_tracks_its_source():
    source, _ = _load_trace()

    assert source == "tests/fixtures/golden/clear_trade/cassette/003-event-000.json..004.json"


def test_recorded_trace_matches_specialist_order_and_tool_policy():
    _, trace = _load_trace()

    assert [step.agent_name for step in trace.steps] == list(SPECIALIST_ORDER)
    assert all(step.status == "completed" for step in trace.steps)
    assert all(
        tool_name != "transfer_to_agent"
        for step in trace.steps
        for tool_name in step.tool_calls
    )

    for step in trace.steps:
        allowed_tool_calls = ALLOWED_TOOL_CALLS.get(step.agent_name, set())
        assert set(step.tool_calls).issubset(allowed_tool_calls)


def test_typed_mode_trace_fixture_matches_specialist_order_and_tool_policy():
    _, trace = _load_trace(TYPED_TRACE_FIXTURE)

    assert [step.agent_name for step in trace.steps] == list(SPECIALIST_ORDER)
    assert all(step.status == "completed" for step in trace.steps)

    for step in trace.steps:
        allowed_tool_calls = ALLOWED_TOOL_CALLS.get(step.agent_name, set())
        assert set(step.tool_calls).issubset(allowed_tool_calls)


def test_typed_mode_trace_records_zero_tokens_and_typed_assessment_output_for_technical_step():
    _, trace = _load_trace(TYPED_TRACE_FIXTURE)

    technical_step = next(step for step in trace.steps if step.agent_name == TECHNICAL_ANALYST_NAME)

    assert technical_step.token_usage == {}
    assert technical_step.output is not None
    assert technical_step.output["source"] == "typed_assessment"
    assert technical_step.output["status"] == "completed"


def test_typed_views_trace_records_zero_tokens_for_both_analyst_steps():
    _, trace = _load_trace(TYPED_VIEWS_TRACE_FIXTURE)

    assert [step.agent_name for step in trace.steps] == list(SPECIALIST_ORDER)
    for agent_name in ("fundamental_analyst", TECHNICAL_ANALYST_NAME):
        step = next(step for step in trace.steps if step.agent_name == agent_name)
        assert step.token_usage == {}
        assert step.output["source"] == "typed_assessment"


def test_typed_synthesizer_trace_has_zero_tokens_and_no_risk_tool_call():
    _, trace = _load_trace(TYPED_SYNTHESIZER_TRACE_FIXTURE)

    assert [step.agent_name for step in trace.steps] == list(SPECIALIST_ORDER)
    for agent_name in ("fundamental_analyst", TECHNICAL_ANALYST_NAME, DECISION_SYNTHESIZER_NAME):
        step = next(step for step in trace.steps if step.agent_name == agent_name)
        assert step.token_usage == {}
        assert step.tool_calls == []
    assert check_synthesizer_step_zero_tokens_in_typed_mode(trace) == []
