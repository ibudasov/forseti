"""Golden-case regression suite for the Technical Analyst evaluation harness.

Mirrors ``tests/test_critic_golden.py``: auto-discovers every fixture under
``tests/fixtures/technical``, asserts the discovered set against a literal
roster so a silently-skipped fixture fails the build, and enforces that the
default (offline) suite never performs network I/O.
"""

from __future__ import annotations

import re
import socket
from datetime import datetime, timezone

import pytest

from app.db.models import AgentRun, AgentRunStep
from scripts import eval_technical_agent as eval_script
from tests.fixtures.technical.loader import (
    build_shadow_report,
    build_shadow_sample,
    evaluate_suite,
    live_technical_case_names,
    live_technical_case_paths,
    load_technical_case,
    load_technical_cases,
    render_suite_markdown,
    run_technical_case,
    technical_case_names,
    technical_case_paths,
)

EXPECTED_CASES = {
    "clean_uptrend_confirmatory",
    "overextended_rsi_far_above_sma50",
    "breakdown_below_swing_low",
    "bearish_divergence",
    "earnings_in_3_days_event_risk",
    "invented_rsi_value",
    "unknown_metric_id",
    "positive_adjustment",
    "forbidden_stop_loss_field",
    "context_hash_mismatch",
    "negative_without_support",
    "stale_bars_short_circuit",
    "malformed_json_then_valid",
    "provider_error_exhausted",
    "enforced_trade_to_watchlist",
    "shadow_no_change",
    "hard_blocker_no_change",
    "workflow_technical_view_reaches_critic",
}

EXPECTED_LIVE_CASES = {
    "clean_uptrend",
    "breakdown",
    "divergence",
}

_RUN_ID_PATTERN = re.compile(r'"run_id":\s*"([^"]+)"')
_CONTEXT_HASH_PATTERN = re.compile(r'"context_hash":\s*"([^"]+)"')

_LIVE_RESPONSES = {
    "run-technical-live-uptrend": (
        '{{"schema_version": "1.0", "run_id": "{run_id}", "context_hash": "{context_hash}", '
        '"agent_name": "technical_analyst", "status": "completed", "overall_signal": "neutral", '
        '"trend_state": "uptrend", "momentum_state": "stable", "proposed_score_adjustment": 0, '
        '"findings": [], "contradictions": [], "risk_flags": [], "missing_information": [], '
        '"summary": "Confirmatory stub read."}}'
    ),
    "run-technical-live-breakdown": (
        '{{"schema_version": "1.0", "run_id": "{run_id}", "context_hash": "{context_hash}", '
        '"agent_name": "technical_analyst", "status": "completed", "overall_signal": "strong_negative", '
        '"trend_state": "downtrend", "momentum_state": "weakening", "proposed_score_adjustment": -2, '
        '"findings": [], "contradictions": [], '
        '"risk_flags": [{{"finding_id": "risk-1", "category": "support_resistance", "direction": "negative", '
        '"materiality": "high", "claim": "Price broke below swing_low_20 at 100.50.", '
        '"metric_ids": ["swing_low_20:2026-09-08"]}}], "missing_information": [], '
        '"summary": "Stub breakdown read."}}'
    ),
    "run-technical-live-divergence": (
        '{{"schema_version": "1.0", "run_id": "{run_id}", "context_hash": "{context_hash}", '
        '"agent_name": "technical_analyst", "status": "completed", "overall_signal": "negative", '
        '"trend_state": "range", "momentum_state": "weakening", "proposed_score_adjustment": -1, '
        '"findings": [], "contradictions": [], '
        '"risk_flags": [{{"finding_id": "risk-1", "category": "momentum", "direction": "negative", '
        '"materiality": "medium", "claim": "A bearish RSI-price divergence has formed.", '
        '"metric_ids": ["rsi_price_divergence_20d:2026-09-08"]}}], "missing_information": [], '
        '"summary": "Stub divergence read."}}'
    ),
}


class _StubTechnicalModel:
    """Replays a canned response keyed by the request's run_id, embedded in the prompt.

    Never opens a socket: the "live" fixtures exercised in ``make test``/CI use
    this stub, not ``GeminiTechnicalModel``. Only ``scripts/eval_technical_agent.py
    --live --confirm-cost yes`` calls the real model.
    """

    model_name = "stub-live-technical"

    def generate(self, prompt: str):
        from app.services.technical_analyst import ModelOutput

        run_id = _RUN_ID_PATTERN.search(prompt).group(1)
        context_hash = _CONTEXT_HASH_PATTERN.search(prompt).group(1)
        template = _LIVE_RESPONSES[run_id]
        text = template.format(run_id=run_id, context_hash=context_hash)
        return ModelOutput(text=text, token_usage={"prompt_token_count": 50, "total_token_count": 80})


@pytest.fixture
def block_network(monkeypatch):
    """Fail the test immediately if anything attempts a real socket connection."""

    def _guarded_connect(self, *args, **kwargs):
        raise AssertionError("Network I/O is forbidden in the offline technical-agent suite.")

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect)


@pytest.mark.usefixtures("block_network")
@pytest.mark.parametrize("case_path", technical_case_paths(), ids=lambda path: path.stem)
def test_technical_fixture_case_passes(case_path):
    case = load_technical_case(case_path)

    result = run_technical_case(case)

    assert result.passed, result.failure_message


@pytest.mark.usefixtures("block_network")
def test_fixture_directory_is_auto_discovered_and_complete():
    discovered = set(technical_case_names())

    assert discovered == EXPECTED_CASES


def test_live_fixture_directory_is_discovered_separately():
    discovered = set(live_technical_case_names())

    assert discovered == EXPECTED_LIVE_CASES


@pytest.mark.usefixtures("block_network")
def test_full_suite_passes_and_renders_all_discovered_cases():
    suite = evaluate_suite(load_technical_cases())
    table = render_suite_markdown(suite)

    assert suite.passed is True
    assert suite.total_cases == len(EXPECTED_CASES)
    for case_name in EXPECTED_CASES:
        assert case_name in table


def test_malformed_fixture_names_the_file_and_problem(tmp_path):
    bad_fixture = tmp_path / "broken_case.json"
    bad_fixture.write_text('{"mode":"enforced"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="broken_case.json: missing non-empty 'case_type'"):
        load_technical_case(bad_fixture)


@pytest.mark.live
@pytest.mark.parametrize("case_path", live_technical_case_paths(), ids=lambda path: path.stem)
def test_live_technical_fixture_case_passes_with_stubbed_model(case_path, block_network):
    case = load_technical_case(case_path)

    result = run_technical_case(case, live=True, model_port=_StubTechnicalModel())

    assert result.passed, result.failure_message


def test_shadow_report_aggregates_adjustments_decisions_and_reason_codes():
    runs = [
        AgentRun(
            run_id="run-1",
            ticker="NVDA",
            created_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            total_latency_ms=10.0,
            token_usage={},
            warnings=[],
            entered_agent_layer=True,
            adk_event_count=1,
            observed_agents=["technical_analyst"],
            technical_agent_effect={
                "mode": "shadow",
                "applied_adjustment": -1,
                "decision_changed": True,
                "reason_codes": ["adjustment_applied", "shadow_mode"],
                "trend_state": "uptrend",
                "momentum_state": "overextended",
            },
        ),
        AgentRun(
            run_id="run-2",
            ticker="IONQ",
            created_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            total_latency_ms=12.0,
            token_usage={},
            warnings=[],
            entered_agent_layer=True,
            adk_event_count=1,
            observed_agents=["technical_analyst"],
            technical_agent_effect={
                "mode": "shadow",
                "applied_adjustment": 0,
                "decision_changed": False,
                "reason_codes": ["assessment_neutral", "shadow_mode"],
                "trend_state": "range",
                "momentum_state": "stable",
            },
        ),
        AgentRun(
            run_id="run-3",
            ticker="PLTR",
            created_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            total_latency_ms=8.0,
            token_usage={},
            warnings=[],
            entered_agent_layer=True,
            adk_event_count=1,
            observed_agents=["technical_analyst"],
            technical_agent_effect={
                "mode": "enforced",
                "applied_adjustment": -2,
                "decision_changed": True,
                "reason_codes": ["adjustment_applied", "enforced_mode"],
                "trend_state": "downtrend",
                "momentum_state": "weakening",
            },
        ),
    ]
    steps = [
        AgentRunStep(
            run_id="run-1",
            sequence=1,
            agent_name="technical_analyst",
            status="completed",
            tool_calls=[],
            latency_ms=100.0,
            token_usage={"prompt_token_count": 5, "candidates_token_count": 5, "total_token_count": 10},
            retries=0,
        ),
        AgentRunStep(
            run_id="run-2",
            sequence=1,
            agent_name="technical_analyst",
            status="completed",
            tool_calls=[],
            latency_ms=50.0,
            token_usage={"prompt_token_count": 3, "candidates_token_count": 3, "total_token_count": 6},
            retries=0,
        ),
    ]

    report = build_shadow_report(runs, steps)

    assert report.run_count == 2
    assert report.decision_change_rate == pytest.approx(0.5)
    assert report.adjustment_distribution["-1"] == 1
    assert report.adjustment_distribution["0"] == 1
    assert report.rejection_reason_counts["shadow_mode"] == 2
    assert report.trend_state_distribution == {"range": 1, "uptrend": 1}
    assert report.momentum_state_distribution == {"overextended": 1, "stable": 1}
    assert report.latency_ms["p50"] > 0
    assert report.token_usage["total_token_count"] == 16.0


def test_shadow_sample_exports_structured_rows_without_raw_output():
    runs = [
        AgentRun(
            run_id="run-1",
            ticker="NVDA",
            created_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            total_latency_ms=10.0,
            token_usage={},
            warnings=[],
            entered_agent_layer=True,
            adk_event_count=1,
            observed_agents=["technical_analyst"],
            technical_agent_effect={
                "mode": "shadow",
                "applied_adjustment": -1,
                "final_decision": "watchlist",
                "decision_changed": True,
                "reason_codes": ["adjustment_applied", "shadow_mode"],
                "trend_state": "uptrend",
                "momentum_state": "overextended",
            },
        ),
    ]

    sample = build_shadow_sample(runs, limit=10)

    assert len(sample) == 1
    row = sample[0].to_dict()
    assert row == {
        "run_id": "run-1",
        "ticker": "NVDA",
        "applied_adjustment": -1,
        "final_decision": "watchlist",
        "decision_changed": True,
        "trend_state": "uptrend",
        "momentum_state": "overextended",
        "reason_codes": "adjustment_applied,shadow_mode",
    }


def test_eval_script_returns_zero_for_default_suite():
    exit_code = eval_script.main(["--json"])

    assert exit_code == 0


def test_eval_script_live_mode_requires_explicit_cost_confirmation():
    with pytest.raises(RuntimeError, match="confirm-cost yes"):
        eval_script.main(["--live"])


def test_eval_script_live_mode_requires_vertex_project(monkeypatch):
    monkeypatch.setattr(eval_script, "get_settings", lambda: type("Settings", (), {"VERTEX_AI_PROJECT": None})())

    with pytest.raises(RuntimeError, match="VERTEX_AI_PROJECT"):
        eval_script.main(["--live", "--confirm-cost", "yes"])
