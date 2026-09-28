from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import AgentRun, AgentRunStep
from scripts import eval_critic as eval_script
from tests.fixtures.critic.loader import (
    build_shadow_report,
    critic_case_names,
    critic_case_paths,
    evaluate_suite,
    load_critic_case,
    render_suite_markdown,
    run_critic_case,
)


EXPECTED_CASES = {
    "clean_accept",
    "analyst_contradiction_downgrade",
    "fabricated_numbers_rejected",
    "upgrade_attempt_rejected",
    "stale_data_watchlist_cap",
    "missing_risk_fields_no_trade",
    "uncited_claim_penalty",
    "revise_then_accept",
    "revise_loop_bounded",
    "model_failure_degrades",
    "invalid_citation_ignored",
    "shadow_mode_no_change",
}


@pytest.mark.parametrize("case_path", critic_case_paths(), ids=lambda path: path.stem)
def test_critic_fixture_case_passes(case_path):
    case = load_critic_case(case_path)

    result = run_critic_case(case)

    assert result.passed, result.failure_message


def test_fixture_directory_is_auto_discovered_and_complete():
    discovered = set(critic_case_names())

    assert discovered == EXPECTED_CASES


def test_full_suite_passes_and_renders_all_twelve_cases():
    suite = evaluate_suite([load_critic_case(path) for path in critic_case_paths()])
    table = render_suite_markdown(suite)

    assert suite.passed is True
    assert suite.total_cases == 12
    assert table.count("| ") >= 13
    for case_name in EXPECTED_CASES:
        assert case_name in table


def test_malformed_fixture_names_the_file_and_problem(tmp_path):
    bad_fixture = tmp_path / "broken_case.json"
    bad_fixture.write_text('{"mode":"enforced"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="broken_case.json: missing non-empty 'case_type'"):
        load_critic_case(bad_fixture)


def test_shadow_report_aggregates_objections_decision_changes_and_severity_counts():
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
            observed_agents=["critic_guardrail"],
            critic_effect={
                "mode": "shadow",
                "objection_counts": {"unsupported_claim": 1},
                "decision_changed": True,
                "deterministic_objection_ids": ["det:1"],
                "model_objection_ids": ["model:1", "model:2"],
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
            observed_agents=["critic_guardrail"],
            critic_effect={
                "mode": "shadow",
                "objection_counts": {},
                "decision_changed": False,
                "deterministic_objection_ids": [],
                "model_objection_ids": [],
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
            observed_agents=["critic_guardrail"],
            critic_effect={
                "mode": "enforced",
                "objection_counts": {"unsupported_claim": 99},
                "decision_changed": True,
                "deterministic_objection_ids": ["det:ignored"],
                "model_objection_ids": ["model:ignored"],
            },
        ),
    ]
    steps = [
        AgentRunStep(
            run_id="run-1",
            sequence=1,
            agent_name="critic_guardrail",
            status="completed",
            tool_calls=[],
            latency_ms=1.0,
            token_usage={},
            retries=0,
            output={"objection_counts_by_severity": {"high": 1, "medium": 1, "low": 0}},
        ),
        AgentRunStep(
            run_id="run-2",
            sequence=1,
            agent_name="critic_guardrail",
            status="completed",
            tool_calls=[],
            latency_ms=1.0,
            token_usage={},
            retries=0,
            output={"objection_counts_by_severity": {"high": 0, "medium": 0, "low": 1}},
        ),
    ]

    report = build_shadow_report(runs, steps)

    assert report.run_count == 2
    assert report.objection_rate == pytest.approx(0.5)
    assert report.decision_change_rate == pytest.approx(0.5)
    assert report.severity_distribution == {"high": 1, "medium": 1, "low": 1}
    assert report.deterministic_vs_model_split == {"deterministic": 1, "model": 2}


def test_eval_script_returns_zero_for_default_suite():
    exit_code = eval_script.main(["--json"])

    assert exit_code == 0


def test_eval_script_live_mode_requires_explicit_cost_confirmation():
    with pytest.raises(RuntimeError, match="confirm-cost yes"):
        eval_script.main(["--live"])
