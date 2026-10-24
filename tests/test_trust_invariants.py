from __future__ import annotations

from datetime import date

import pytest

from app.schemas.analyze import AnalysisTrace, AnalyzeResponse, DecisionDiagnosis, TraceStep
from app.schemas.critique import CriticEffect, CriticVersionStamp
from app.schemas.technicals import TechnicalAgentEffect, TechnicalAgentVersionStamp, TechnicalAssessmentResponse
from app.trust.invariants import (
    CRITIC_OFF_ALLOWED_SKIPS,
    check_critic_shadow_persistence,
    check_data_gate_warning_propagation,
    check_downgrade_only_behavior,
    check_fundamental_agent_authority,
    check_risk_field_equality,
    check_specialist_sequence,
    check_technical_agent_effect_rank,
    check_technical_numeric_claims_grounded,
    check_technical_risk_fields_untouched_in_shadow,
    check_technical_step_zero_tokens_in_typed_mode,
    evaluate_agentic_invariants,
)
from tests.fixtures.technical.factory import build_request


@pytest.mark.parametrize(
    "changes,expected_code",
    [
        ({}, None),
        ({"counterfactual_decision": "trade"}, "fundamental_agent_effect_rank_upgrade"),
        ({"final_decision": "trade"}, "fundamental_agent_effect_rank_upgrade"),
        ({"applied_adjustment": 1}, "fundamental_positive_adjustment"),
    ],
)
def test_fundamental_authority_is_checked_even_if_later_agents_downgrade(changes, expected_code):
    from app.services.fundamental_evaluation import load_fundamental_evaluation_suite
    from app.services.fundamental_policy import apply_fundamental_policy

    case = load_fundamental_evaluation_suite().cases[2]
    effect = apply_fundamental_policy(
        mode="shadow",
        deterministic_response=case.deterministic_response,
        request=case.request,
        assessment_result=case.recorded_result,
    )
    response = _response(decision="no_trade")
    response.fundamental_agent_effect = effect.model_copy(update=changes)

    violations = check_fundamental_agent_authority(response)

    assert [violation.code for violation in violations] == ([] if expected_code is None else [expected_code])
    aggregated = evaluate_agentic_invariants(
        deterministic_response=_response(), agentic_response=response, trace=_trace()
    )
    if expected_code is not None:
        assert expected_code in {violation.code for violation in aggregated}


def _response(
    *,
    decision: str = "trade",
    confidence: float = 0.7,
    warnings: list[str] | None = None,
    entry_range: tuple[float, float] | None = (10.0, 11.0),
    stop_loss: float | None = 9.0,
    take_profit: tuple[float, float] | None = (12.0, 13.0),
    risk_reward: float | None = 2.0,
    position_size_eur: float | None = 100.0,
) -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision=decision,
        confidence=confidence,
        reasons=["baseline"],
        warnings=warnings or [],
        entry_range=entry_range,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        position_size_eur=position_size_eur,
        engine_version="v1.rules.0",
        trace_id="run-123",
        time_stop_at=date(2026, 10, 31) if decision == "trade" else None,
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="checklist_passed",
            detail="fixture",
            checklist_score=8,
            debug_reason="fixture",
        ),
    )


def _trace(*, critic_status: str = "completed", critic_reason: str | None = None) -> AnalysisTrace:
    critic_output = {"reason": critic_reason} if critic_reason is not None else {"summary": "ok"}
    return AnalysisTrace(
        run_id="run-123",
        ticker="NVDA",
        final_decision="trade",
        steps=[
            TraceStep(sequence=1, agent_name="fundamental_analyst", status="completed"),
            TraceStep(sequence=2, agent_name="technical_analyst", status="completed"),
            TraceStep(sequence=3, agent_name="decision_synthesizer", status="completed"),
            TraceStep(
                sequence=4,
                agent_name="critic_guardrail",
                status=critic_status,
                output=critic_output,
            ),
        ],
    )


def _shadow_effect(
    *,
    mode: str = "shadow",
    revisions_requested: int = 0,
    revisions_performed: int = 0,
) -> CriticEffect:
    return CriticEffect(
        mode=mode,
        accepted=True,
        status="completed",
        verdict="accept",
        reason_codes=[],
        objection_counts={},
        deterministic_objection_ids=[],
        model_objection_ids=[],
        baseline_decision="trade",
        proposed_decision=None,
        final_decision="trade",
        decision_changed=False,
        baseline_confidence=0.7,
        applied_confidence_penalty=0.0,
        final_confidence=0.7,
        revisions_requested=revisions_requested,
        revisions_performed=revisions_performed,
        versions=CriticVersionStamp(
            request_schema_version="1.0",
            critique_schema_version="1.0",
            prompt_version="critic-guardrail.v1",
            model_name="fixture-critic",
            policy_version="1.0",
        ),
    )


def _technical_effect(
    *,
    mode: str = "shadow",
    baseline_decision: str = "trade",
    final_decision: str = "trade",
    decision_changed: bool = False,
    baseline_confidence: float = 0.7,
    final_confidence: float = 0.7,
) -> TechnicalAgentEffect:
    return TechnicalAgentEffect(
        mode=mode,
        accepted=True,
        assessment_status="completed",
        overall_signal="negative",
        trend_state="uptrend",
        momentum_state="overextended",
        reason_codes=["shadow_mode"] if mode == "shadow" else [],
        missing_information=[],
        raw_adjustment=-1,
        applied_adjustment=-1,
        baseline_technical_score=1,
        adjusted_technical_score=0,
        baseline_total_score=8,
        adjusted_total_score=7,
        baseline_decision=baseline_decision,
        counterfactual_decision="watchlist",
        final_decision=final_decision,
        decision_changed=decision_changed,
        baseline_confidence=baseline_confidence,
        final_confidence=final_confidence,
        versions=TechnicalAgentVersionStamp(
            context_schema_version="1.0",
            assessment_schema_version="1.0",
            prompt_version="technical-analyst.v1",
            model_name="fixture-technical",
            policy_version="1.0",
        ),
    )


_TECHNICAL_REQUEST_PAYLOAD = {
    "run_id": "run-123",
    "ticker": "NVDA",
    "sector": "ai",
    "snapshot_at": "2026-09-08T23:59:59Z",
    "as_of_date": "2026-09-08",
    "deterministic_result": {
        "ticker": "NVDA",
        "as_of_date": "2026-09-08",
        "score": 1,
        "maximum_score": 5,
        "rule_results": [
            {
                "rule_id": "volume_trend",
                "status": "passed",
                "points_awarded": 1,
                "points_available": 1,
                "metric_ids": ["volume_trend:2026-09-08"],
                "explanation": "volume_trend 1.20 >= 1.0 minimum",
            }
        ],
        "warnings": [],
        "metrics": [
            {
                "metric_id": "volume_trend:2026-09-08",
                "metric_name": "volume_trend",
                "value": "1.20",
                "unit": "ratio",
                "as_of_date": "2026-09-08",
            }
        ],
    },
    "facts": [
        {
            "metric_id": "rsi_14:2026-09-08",
            "name": "rsi_14",
            "value": "68.00",
            "unit": "rsi_points",
            "derivation": "Wilder RSI-14",
        }
    ],
    "series": [],
    "coverage": {
        "bar_count": 200,
        "required_facts_missing": [],
        "latest_bar_date": "2026-09-08",
        "bars_stale": False,
        "series_truncated": False,
    },
}


def _technical_finding(*, finding_id: str, claim: str, metric_ids: list[str]) -> dict:
    return {
        "finding_id": finding_id,
        "category": "overextension",
        "direction": "negative",
        "materiality": "high",
        "claim": claim,
        "metric_ids": metric_ids,
    }


def _technical_assessment_response(*, findings: list[dict]) -> TechnicalAssessmentResponse:
    request = build_request(_TECHNICAL_REQUEST_PAYLOAD)
    return TechnicalAssessmentResponse.model_validate(
        {
            "run_id": request.run_id,
            "context_hash": request.context_hash,
            "status": "completed",
            "overall_signal": "negative",
            "trend_state": "uptrend",
            "momentum_state": "overextended",
            "proposed_score_adjustment": -1,
            "findings": [],
            "contradictions": [],
            "risk_flags": findings,
            "missing_information": [],
            "summary": "Overextended momentum despite a constructive trend.",
        }
    )


def test_risk_field_equality_passes_for_identical_numbers():
    deterministic = _response()
    agentic = _response()

    assert check_risk_field_equality(deterministic, agentic) == []


def test_risk_field_equality_fails_when_any_number_changes():
    deterministic = _response()
    agentic = _response(stop_loss=8.5)

    violations = check_risk_field_equality(deterministic, agentic)

    assert [violation.code for violation in violations] == ["risk_field_mismatch"]
    assert violations[0].details["field_name"] == "stop_loss"


def test_downgrade_only_behavior_rejects_decision_and_confidence_upgrades():
    deterministic = _response(decision="watchlist", confidence=0.4)
    agentic = _response(decision="trade", confidence=0.5)

    violations = check_downgrade_only_behavior(deterministic, agentic)

    assert {violation.code for violation in violations} == {"decision_upgrade", "confidence_upgrade"}


def test_specialist_sequence_fails_when_required_step_is_missing():
    trace = AnalysisTrace(
        run_id="run-123",
        ticker="NVDA",
        final_decision="trade",
        steps=[
            TraceStep(sequence=1, agent_name="fundamental_analyst", status="completed"),
            TraceStep(sequence=3, agent_name="decision_synthesizer", status="completed"),
            TraceStep(sequence=4, agent_name="critic_guardrail", status="completed"),
        ],
    )

    violations = check_specialist_sequence(trace)

    assert [violation.code for violation in violations] == ["missing_specialist_step"]
    assert violations[0].details["agent_name"] == "technical_analyst"


def test_specialist_sequence_allows_critic_skip_when_mode_is_off():
    violations = check_specialist_sequence(
        _trace(critic_status="skipped", critic_reason="critic_mode_off"),
        allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
    )

    assert violations == []


def test_data_gate_warning_propagation_requires_agentic_warning_superset():
    deterministic = _response(warnings=["no_price_data", "no_earnings_data"])
    agentic = _response(warnings=["no_earnings_data"])

    violations = check_data_gate_warning_propagation(deterministic, agentic)

    assert [violation.code for violation in violations] == ["missing_data_gate_warning"]
    assert violations[0].details["missing_warnings"] == ["no_price_data"]


def test_evaluate_agentic_invariants_aggregates_every_check():
    deterministic = _response(decision="watchlist", confidence=0.4, warnings=["stale_price_data"])
    agentic = _response(decision="trade", confidence=0.5, stop_loss=8.5, warnings=[])
    trace = _trace(critic_status="skipped", critic_reason="other_reason")

    violations = evaluate_agentic_invariants(
        deterministic_response=deterministic,
        agentic_response=agentic,
        trace=trace,
        allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
    )

    assert {violation.code for violation in violations} == {
        "specialist_skipped",
        "risk_field_mismatch",
        "decision_upgrade",
        "confidence_upgrade",
        "missing_data_gate_warning",
    }


def test_critic_shadow_persistence_accepts_matching_persisted_effect():
    effect = _shadow_effect()
    response = _response().model_copy(update={"critic_effect": effect})
    trace = _trace().model_copy(update={"critic_effect": effect})

    assert check_critic_shadow_persistence(response, trace) == []


def test_critic_shadow_persistence_rejects_missing_or_excessive_revision_state():
    response = _response().model_copy(update={"critic_effect": _shadow_effect(revisions_performed=2)})
    trace = _trace().model_copy(update={"critic_effect": None})

    violations = check_critic_shadow_persistence(response, trace)

    assert {violation.code for violation in violations} == {
        "missing_persisted_critic_effect",
        "critic_revision_limit_exceeded",
    }


def test_technical_agent_effect_rank_passes_for_downgrade_only_effect():
    response = _response().model_copy(
        update={"technical_agent_effect": _technical_effect(baseline_decision="trade", final_decision="watchlist")}
    )

    assert check_technical_agent_effect_rank(response) == []


def test_technical_agent_effect_rank_rejects_upgrade():
    response = _response().model_copy(
        update={"technical_agent_effect": _technical_effect(baseline_decision="watchlist", final_decision="trade")}
    )

    violations = check_technical_agent_effect_rank(response)

    assert [violation.code for violation in violations] == ["technical_agent_effect_rank_upgrade"]


def test_technical_risk_fields_untouched_in_shadow_passes_when_baseline_is_preserved():
    response = _response().model_copy(update={"technical_agent_effect": _technical_effect(mode="shadow")})

    assert check_technical_risk_fields_untouched_in_shadow(response) == []


def test_technical_risk_fields_untouched_in_shadow_ignores_non_shadow_modes():
    effect = _technical_effect(mode="enforced", baseline_decision="trade", final_decision="watchlist", decision_changed=True)
    response = _response().model_copy(update={"technical_agent_effect": effect})

    assert check_technical_risk_fields_untouched_in_shadow(response) == []


def test_technical_risk_fields_untouched_in_shadow_rejects_mutated_decision_and_confidence():
    effect = _technical_effect(
        mode="shadow",
        baseline_decision="trade",
        final_decision="watchlist",
        decision_changed=True,
        baseline_confidence=0.7,
        final_confidence=0.5,
    )
    response = _response().model_copy(update={"technical_agent_effect": effect})

    violations = check_technical_risk_fields_untouched_in_shadow(response)

    assert {violation.code for violation in violations} == {
        "technical_shadow_decision_mutated",
        "technical_shadow_decision_changed_flag_set",
        "technical_shadow_confidence_mutated",
    }


def test_technical_numeric_claims_grounded_passes_for_cited_number():
    response = _technical_assessment_response(
        findings=[
            _technical_finding(
                finding_id="risk-1",
                claim="RSI at 68.00 signals an overextended uptrend.",
                metric_ids=["rsi_14:2026-09-08"],
            )
        ]
    )
    request = build_request(_TECHNICAL_REQUEST_PAYLOAD)

    assert check_technical_numeric_claims_grounded(response, request) == []


def test_technical_numeric_claims_grounded_rejects_ungrounded_number():
    response = _technical_assessment_response(
        findings=[
            _technical_finding(
                finding_id="risk-1",
                claim="RSI at 91.00 signals an overextended uptrend.",
                metric_ids=["rsi_14:2026-09-08"],
            )
        ]
    )
    request = build_request(_TECHNICAL_REQUEST_PAYLOAD)

    violations = check_technical_numeric_claims_grounded(response, request)

    assert [violation.code for violation in violations] == ["technical_numeric_claim_ungrounded"]


def test_technical_step_zero_tokens_in_typed_mode_passes_for_zero_token_step():
    trace = _trace().model_copy(
        update={
            "steps": [
                TraceStep(
                    sequence=2,
                    agent_name="technical_analyst",
                    status="completed",
                    output={"source": "typed_assessment", "status": "completed"},
                    token_usage={},
                )
            ]
        }
    )

    assert check_technical_step_zero_tokens_in_typed_mode(trace) == []


def test_technical_step_zero_tokens_in_typed_mode_ignores_non_typed_steps():
    trace = _trace().model_copy(
        update={
            "steps": [
                TraceStep(
                    sequence=2,
                    agent_name="technical_analyst",
                    status="completed",
                    output={"status": "completed"},
                    token_usage={"total_token_count": 42},
                )
            ]
        }
    )

    assert check_technical_step_zero_tokens_in_typed_mode(trace) == []


def test_technical_step_zero_tokens_in_typed_mode_rejects_nonzero_tokens():
    trace = _trace().model_copy(
        update={
            "steps": [
                TraceStep(
                    sequence=2,
                    agent_name="technical_analyst",
                    status="completed",
                    output={"source": "typed_assessment", "status": "completed"},
                    token_usage={"total_token_count": 42},
                )
            ]
        }
    )

    violations = check_technical_step_zero_tokens_in_typed_mode(trace)

    assert [violation.code for violation in violations] == ["technical_typed_view_step_nonzero_tokens"]
    assert violations[0].details["token_usage"] == {"total_token_count": 42}
