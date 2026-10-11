from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.schemas.critique import CritiqueObjection, CritiqueResponse, CritiqueResult, CritiqueValidation
from app.db.models import AgentRun, AgentRunStep
from scripts import eval_critic as eval_script
from tests.fixtures.critic.loader import (
    build_shadow_report,
    build_shadow_sample,
    critic_case_names,
    critic_case_paths,
    evaluate_suite,
    load_critic_case,
    load_critic_cases,
    live_critic_case_names,
    live_critic_case_paths,
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
    "no_price_data_watchlist_cap",
    "uncited_claim_penalty",
    "revise_then_accept",
    "revise_loop_bounded",
    "model_failure_degrades",
    "invalid_citation_ignored",
    "shadow_mode_no_change",
}

EXPECTED_LIVE_CASES = {
    "clean_trade_live",
    "uncited_claim_live",
    "risk_mismatch_live",
}


class _StubCritic:
    def review(self, request):
        objection_by_ticker = {
            "SERV": [
                CritiqueObjection.model_construct(
                    objection_id="model:uncited-1",
                    category="unsupported_claim",
                    severity="medium",
                    source="model",
                    claim="Material claim lacks supporting evidence.",
                    metric_ids=["revenue_growth:2026-06-30"],
                    chunk_ids=[],
                    warning_codes=[],
                )
            ],
        }
        objections = objection_by_ticker.get(request.ticker, [])
        return CritiqueResult(
            response=CritiqueResponse.model_construct(
                schema_version="1.0",
                run_id=request.run_id,
                context_hash=request.context_hash,
                agent_name="critic_guardrail",
                status="completed",
                verdict="accept",
                objections=objections,
                proposed_decision=None,
                proposed_confidence_penalty=0.0,
                revision_instructions="",
                summary="Live stub summary.",
            ),
            validation=CritiqueValidation(accepted=True, reason_codes=[]),
            raw_output="{}",
            latency_ms=42.0,
            token_usage={"prompt_token_count": 5, "total_token_count": 11},
            model_name="stub-live-critic",
            prompt_version="critic-guardrail.v1",
        )


@pytest.mark.parametrize("case_path", critic_case_paths(), ids=lambda path: path.stem)
def test_critic_fixture_case_passes(case_path):
    case = load_critic_case(case_path)

    result = run_critic_case(case)

    assert result.passed, result.failure_message


def test_fixture_directory_is_auto_discovered_and_complete():
    discovered = set(critic_case_names())

    assert discovered == EXPECTED_CASES


def test_live_fixture_directory_is_discovered_separately():
    discovered = set(live_critic_case_names())

    assert discovered == EXPECTED_LIVE_CASES


def test_new_fixture_records_its_source():
    case = load_critic_case(Path(__file__).resolve().parent / "fixtures" / "critic" / "no_price_data_watchlist_cap.json")

    assert case.source.startswith("docs/trust/e2e-review.md")


def test_full_suite_passes_and_renders_all_twelve_cases():
    suite = evaluate_suite(load_critic_cases())
    table = render_suite_markdown(suite)

    assert suite.passed is True
    assert suite.total_cases == len(EXPECTED_CASES)
    assert table.count("| ") >= suite.total_cases + 1
    for case_name in EXPECTED_CASES:
        assert case_name in table


def test_malformed_fixture_names_the_file_and_problem(tmp_path):
    bad_fixture = tmp_path / "broken_case.json"
    bad_fixture.write_text('{"mode":"enforced"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="broken_case.json: missing non-empty 'case_type'"):
        load_critic_case(bad_fixture)


@pytest.mark.live
@pytest.mark.parametrize("case_path", live_critic_case_paths(), ids=lambda path: path.stem)
def test_live_critic_fixture_case_passes_with_stubbed_model(case_path):
    case = load_critic_case(case_path)

    result = run_critic_case(case, live=True, critic=_StubCritic())

    assert result.passed, result.failure_message
    assert result.latency_ms == pytest.approx(42.0)
    assert result.token_usage == {"prompt_token_count": 5, "total_token_count": 11}


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


def test_shadow_sample_exports_structured_objections_without_raw_evidence():
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
                "model_objection_ids": ["model:1"],
            },
        )
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
            output={
                "objection_counts_by_severity": {"high": 1, "medium": 0, "low": 0},
                "objections": [
                    {
                        "objection_code": "model:1",
                        "severity": "high",
                        "source": "model",
                        "reason": "unsupported_claim: Missing citation (metrics=revenue_growth:2026-06-30)",
                    }
                ],
            },
        )
    ]

    sample = build_shadow_sample(runs, steps, limit=5)

    assert [row.to_dict() for row in sample] == [
        {
            "run_id": "run-1",
            "ticker": "NVDA",
            "objection_code": "model:1",
            "severity": "high",
            "source": "model",
            "reason": "unsupported_claim: Missing citation (metrics=revenue_growth:2026-06-30)",
            "label": "",
        }
    ]


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
