from __future__ import annotations

from datetime import date

from app.schemas.analyze import AnalysisTrace, AnalyzeResponse, DecisionDiagnosis, TraceStep
from app.schemas.critique import CriticEffect, CriticVersionStamp
from app.trust.invariants import (
    CRITIC_OFF_ALLOWED_SKIPS,
    check_critic_shadow_persistence,
    check_data_gate_warning_propagation,
    check_downgrade_only_behavior,
    check_risk_field_equality,
    check_specialist_sequence,
    evaluate_agentic_invariants,
)


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
