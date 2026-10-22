from __future__ import annotations

import json
from pathlib import Path

from agents.orchestration.registry import DECISION_SYNTHESIZER_NAME, SPECIALIST_ORDER
from app.schemas.analyze import AnalysisTrace

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "trust" / "clear_trade_trajectory.json"
ALLOWED_TOOL_CALLS = {
    DECISION_SYNTHESIZER_NAME: {"calculate_risk"},
}


def _load_trace() -> tuple[str, AnalysisTrace]:
    payload = json.loads(TRACE_FIXTURE.read_text(encoding="utf-8"))
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
