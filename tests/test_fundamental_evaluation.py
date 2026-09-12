from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import AgentRun
from app.services.fundamental_evaluation import (
    DEFAULT_EVALUATION_FIXTURE,
    build_shadow_report,
    evaluate_recorded_suite,
    load_fundamental_evaluation_suite,
    validate_fundamental_evaluation_suite,
)
from app.services.fundamental_context import compute_fundamental_context_hash
from scripts import eval_fundamental_agent as eval_script


def test_default_suite_is_valid_and_uses_frozen_hashes():
    suite = load_fundamental_evaluation_suite()

    validate_fundamental_evaluation_suite(suite)

    assert suite.schema_version == "1.0"
    assert len(suite.cases) == 11


def test_recorded_suite_reports_expected_metrics_and_passes_gates():
    suite = load_fundamental_evaluation_suite()

    report = evaluate_recorded_suite(suite)

    assert report.gates.passed is True
    assert report.metrics.case_count == 11
    assert report.metrics.structured_output_validity_rate == 1.0
    assert report.metrics.citation_validity_rate == 1.0
    assert report.metrics.material_claim_citation_coverage == 1.0
    assert report.metrics.unsupported_claim_rate == 0.0
    assert report.metrics.abstention_rate == pytest.approx(2 / 11)
    assert report.metrics.appropriate_abstention_precision == 1.0
    assert report.metrics.agreement_rate == 1.0
    assert report.metrics.duplicate_evidence_rejection_rate == pytest.approx(1 / 11)
    assert report.metrics.hard_blocker_violation_count == 0
    assert report.metrics.risk_field_mutation_count == 0
    assert report.metrics.provider_failure_rate == pytest.approx(1 / 11)
    assert report.metrics.neutral_fallback_rate == pytest.approx(2 / 11)
    assert report.metrics.adjustment_distribution == {
        "-2": 0,
        "-1": 2,
        "0": 4,
        "1": 4,
        "2": 1,
    }
    assert report.metrics.outcome_counts == {
        "downgrade": 2,
        "promotion": 2,
        "unchanged": 7,
    }
    assert report.metrics.status_counts == {
        "completed": 9,
        "failed": 1,
        "insufficient_data": 1,
    }
    assert report.metrics.counterfactual_confusion_matrix == {
        "no_trade->no_trade": 2,
        "no_trade->watchlist": 1,
        "trade->trade": 1,
        "trade->watchlist": 2,
        "watchlist->trade": 1,
        "watchlist->watchlist": 4,
    }
    assert len(report.review_samples) == 5


def test_suite_validation_rejects_future_dated_evidence():
    suite = load_fundamental_evaluation_suite()
    case = suite.cases[0]
    chunk = case.request.evidence_chunks[0]
    invalid_chunk = chunk.model_copy(
        update={"published_at": case.request.snapshot_at + timedelta(seconds=1)}
    )
    invalid_case = case.model_copy(
        update={
            "request": case.request.model_copy(
                update={
                    "evidence_chunks": [invalid_chunk],
                    "context_hash": compute_fundamental_context_hash(
                        case.request.model_copy(update={"evidence_chunks": [invalid_chunk]})
                    ),
                }
            ),
        }
    )
    invalid_suite = suite.model_copy(update={"cases": [invalid_case, *suite.cases[1:]]})

    with pytest.raises(ValueError, match="future_dated_evidence"):
        validate_fundamental_evaluation_suite(invalid_suite)


def test_shadow_report_aggregates_outcomes_versions_and_missing_values():
    suite = load_fundamental_evaluation_suite()
    reference_case = suite.cases[0]
    reference_effect = {
        "mode": "shadow",
        "accepted": True,
        "assessment_status": "completed",
        "overall_signal": "positive",
        "reason_codes": ["adjustment_applied", "shadow_mode"],
        "missing_information": [],
        "source_types_missing": [],
        "raw_adjustment": 1,
        "applied_adjustment": 1,
        "baseline_fundamental_score": 2,
        "adjusted_fundamental_score": 3,
        "baseline_total_score": 7,
        "adjusted_total_score": 8,
        "baseline_decision": "watchlist",
        "counterfactual_decision": "trade",
        "final_decision": "watchlist",
        "decision_changed": False,
        "versions": {
            "context_schema_version": "1.0",
            "assessment_schema_version": "1.0",
            "prompt_version": "fundamental-analyst.v1",
            "model_name": "frozen-fixture",
            "policy_version": "1.0",
        },
    }
    missing_info_effect = dict(reference_effect)
    missing_info_effect.update(
        {
            "assessment_status": "insufficient_data",
            "reason_codes": ["insufficient_evidence", "shadow_mode"],
            "missing_information": ["evidence:growth_sustainability"],
            "source_types_missing": ["earnings_transcript"],
            "raw_adjustment": 0,
            "applied_adjustment": 0,
            "counterfactual_decision": "watchlist",
        }
    )
    runs = [
        AgentRun(
            run_id="run-1",
            ticker=reference_case.request.ticker,
            created_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
            total_latency_ms=150.0,
            token_usage={
                "prompt_token_count": 100,
                "candidates_token_count": 50,
                "total_token_count": 150,
            },
            warnings=[],
            entered_agent_layer=True,
            adk_event_count=1,
            observed_agents=["fundamental_analyst"],
            fundamental_agent_effect=reference_effect,
        ),
        AgentRun(
            run_id="run-2",
            ticker="IONQ",
            created_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
            total_latency_ms=0.0,
            token_usage={},
            warnings=[],
            entered_agent_layer=True,
            adk_event_count=1,
            observed_agents=["fundamental_analyst"],
            fundamental_agent_effect=missing_info_effect,
        ),
    ]

    report = build_shadow_report(runs)

    assert report.run_count == 2
    assert report.outcome_counts == {"promotion": 1, "unchanged": 1}
    assert report.status_counts == {"completed": 1, "insufficient_data": 1}
    assert report.rejection_reason_counts["shadow_mode"] == 2
    assert report.missing_information_counts["evidence:growth_sustainability"] == 1
    assert report.missing_information_counts["source:earnings_transcript"] == 1
    assert report.version_breakdown == {
        "model=frozen-fixture|prompt=fundamental-analyst.v1|context=1.0|assessment=1.0|policy=1.0": 2
    }
    assert report.latency_ms == {"p50": 150.0, "p90": 150.0, "max": 150.0}
    assert report.token_usage["total_token_count"] == 150.0


def test_eval_script_returns_zero_for_recorded_fixture():
    exit_code = eval_script.main(["--fixture", str(DEFAULT_EVALUATION_FIXTURE), "--json"])

    assert exit_code == 0


def test_eval_script_live_mode_requires_explicit_cost_confirmation():
    with pytest.raises(RuntimeError, match="confirm-cost yes"):
        eval_script.main(["--live"])
