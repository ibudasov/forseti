from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.schemas.analyze import AnalyzeResponse
from app.schemas.critique import (
    CritiqueObjection,
    CritiqueValidation,
    CritiqueResult,
    CritiqueRequest,
    CritiqueResponse,
    DraftRecommendation,
)
from app.services.critic_policy import (
    ANALYST_CONTRADICTION_CONFIDENCE_PENALTY,
    MEDIUM_OBJECTION_CONFIDENCE_PENALTY,
    apply_critic_policy,
)


def test_risk_number_mismatch_restores_baseline():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(decision="trade", confidence=0.72),
        request=_request(),
        baseline=_baseline(decision="watchlist", confidence=0.61),
        critique_result=_critique_result(),
        deterministic_objections=[_deterministic_objection("risk_number_mismatch", "high")],
    )

    assert effect.final_decision == "watchlist"
    assert effect.final_confidence == 0.61
    assert "guardrail_rejected: risk_number_mismatch" in effect.reason_codes


def test_decision_upgrade_attempt_restores_baseline():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(decision="trade"),
        request=_request(),
        baseline=_baseline(decision="watchlist"),
        critique_result=_critique_result(),
        deterministic_objections=[
            _deterministic_objection(
                "internal_inconsistency",
                "high",
                warning_codes=["decision_upgrade:watchlist:trade"],
            )
        ],
    )

    assert effect.final_decision == "watchlist"
    assert "guardrail_rejected: decision_upgrade_attempt" in effect.reason_codes


def test_missing_trade_fields_force_no_trade():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(decision="trade"),
        request=_request(),
        baseline=_baseline(decision="trade"),
        critique_result=_critique_result(),
        deterministic_objections=[
            _deterministic_objection(
                "internal_inconsistency",
                "high",
                warning_codes=["missing_risk_field:stop_loss"],
            )
        ],
    )

    assert effect.final_decision == "no_trade"
    assert "guardrail_forced_no_trade" in effect.reason_codes


def test_stale_data_caps_trade_to_watchlist():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(decision="trade"),
        request=_request(),
        baseline=_baseline(decision="trade"),
        critique_result=_critique_result(),
        deterministic_objections=[_deterministic_objection("stale_or_incomplete_data", "high")],
    )

    assert effect.final_decision == "watchlist"
    assert "guardrail_capped_watchlist" in effect.reason_codes


def test_analyst_contradiction_caps_watchlist_and_penalizes_confidence():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(decision="trade", confidence=0.80),
        request=_request(),
        baseline=_baseline(decision="trade", confidence=0.80),
        critique_result=_critique_result(),
        deterministic_objections=[_deterministic_objection("analyst_contradiction", "high")],
    )

    assert effect.final_decision == "watchlist"
    assert effect.applied_confidence_penalty == ANALYST_CONTRADICTION_CONFIDENCE_PENALTY
    assert effect.final_confidence == 0.65
    assert "guardrail_analyst_contradiction" in effect.reason_codes


def test_medium_objections_apply_summed_confidence_penalty():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(confidence=0.80),
        request=_request(),
        baseline=_baseline(confidence=0.80),
        critique_result=_critique_result(),
        deterministic_objections=[
            _deterministic_objection("unsupported_claim", "medium", objection_id="det:1"),
            _deterministic_objection("unsupported_claim", "medium", objection_id="det:2"),
        ],
    )

    assert effect.applied_confidence_penalty == 2 * MEDIUM_OBJECTION_CONFIDENCE_PENALTY
    assert effect.final_confidence == pytest.approx(0.70)
    assert "guardrail_confidence_penalty" in effect.reason_codes


def test_first_match_wins_for_decision_actions():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(decision="trade", confidence=0.70),
        request=_request(),
        baseline=_baseline(decision="watchlist", confidence=0.55),
        critique_result=_critique_result(),
        deterministic_objections=[
            _deterministic_objection("risk_number_mismatch", "high", objection_id="det:1"),
            _deterministic_objection(
                "internal_inconsistency",
                "high",
                objection_id="det:2",
                warning_codes=["missing_risk_field:stop_loss"],
            ),
            _deterministic_objection("stale_or_incomplete_data", "high", objection_id="det:3"),
        ],
    )

    assert effect.final_decision == "watchlist"
    assert effect.final_confidence == 0.55
    assert effect.reason_codes.count("guardrail_rejected: risk_number_mismatch") == 1
    assert "guardrail_forced_no_trade" not in effect.reason_codes


def test_final_decision_never_exceeds_baseline_decision():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(decision="watchlist"),
        request=_request(),
        baseline=_baseline(decision="watchlist"),
        critique_result=_critique_result(
            verdict="reject",
            proposed_decision="trade",
            objections=[_model_objection()],
        ),
        deterministic_objections=[],
    )

    assert effect.final_decision == "watchlist"


def test_final_confidence_never_exceeds_baseline_confidence():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(confidence=0.70),
        request=_request(),
        baseline=_baseline(confidence=0.70),
        critique_result=_critique_result(penalty=0.10),
        deterministic_objections=[],
    )

    assert effect.final_confidence <= effect.baseline_confidence


def test_final_confidence_is_clamped_to_unit_interval():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(confidence=0.04),
        request=_request(),
        baseline=_baseline(confidence=0.04),
        critique_result=_critique_result(penalty=0.30),
        deterministic_objections=[_deterministic_objection("unsupported_claim", "medium")],
    )

    assert effect.final_confidence == 0.0


def test_total_penalty_is_clamped_to_request_maximum():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(confidence=0.90),
        request=_request(max_confidence_penalty=0.30),
        baseline=_baseline(confidence=0.90),
        critique_result=_critique_result(penalty=0.90),
        deterministic_objections=[_deterministic_objection("unsupported_claim", "medium")],
    )

    assert effect.applied_confidence_penalty == 0.30


def test_money_fields_are_not_mutated():
    response = _response()
    before = response.model_dump(mode="json")

    apply_critic_policy(
        mode="enforced",
        response=response,
        request=_request(),
        baseline=_baseline(),
        critique_result=_critique_result(
            verdict="reject",
            proposed_decision="no_trade",
            objections=[_model_objection()],
        ),
        deterministic_objections=[],
    )

    after = response.model_dump(mode="json")
    for field_name in (
        "entry_range",
        "stop_loss",
        "take_profit",
        "risk_reward",
        "position_size_eur",
    ):
        assert after[field_name] == before[field_name]


def test_off_mode_ignores_model_verdict_but_keeps_deterministic_effect():
    effect = apply_critic_policy(
        mode="off",
        response=_response(decision="trade"),
        request=_request(),
        baseline=_baseline(decision="trade"),
        critique_result=_critique_result(
            verdict="reject",
            proposed_decision="no_trade",
            objections=[_model_objection()],
        ),
        deterministic_objections=[_deterministic_objection("stale_or_incomplete_data", "high")],
    )

    assert effect.final_decision == "watchlist"
    assert effect.revisions_requested == 0


def test_shadow_mode_computes_model_effect_counterfactually():
    effect = apply_critic_policy(
        mode="shadow",
        response=_response(decision="trade"),
        request=_request(),
        baseline=_baseline(decision="trade"),
        critique_result=_critique_result(
            verdict="reject",
            proposed_decision="no_trade",
            penalty=0.20,
            objections=[_model_objection()],
        ),
        deterministic_objections=[],
    )

    assert effect.final_decision == "no_trade"
    assert effect.applied_confidence_penalty == 0.20


def test_enforced_mode_requests_single_revision():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(),
        request=_request(),
        baseline=_baseline(),
        critique_result=_critique_result(
            verdict="revise",
            revision_instructions="Reduce confidence.",
            objections=[_model_objection()],
        ),
        deterministic_objections=[],
    )

    assert effect.revisions_requested == 1


def test_invalid_validation_ignores_model_half_and_records_assessment_invalid():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(decision="trade"),
        request=_request(),
        baseline=_baseline(decision="trade"),
        critique_result=_critique_result(
            accepted=False,
            verdict="reject",
            proposed_decision="no_trade",
            reason_codes=["provider_error:timeout"],
            objections=[_model_objection()],
        ),
        deterministic_objections=[_deterministic_objection("stale_or_incomplete_data", "high")],
    )

    assert effect.final_decision == "watchlist"
    assert "assessment_invalid" in effect.reason_codes
    assert "provider_error:timeout" in effect.reason_codes


def test_invalid_upgrade_proposal_is_ignored_and_recorded():
    effect = apply_critic_policy(
        mode="enforced",
        response=_response(decision="watchlist"),
        request=_request(),
        baseline=_baseline(decision="watchlist"),
        critique_result=_critique_result(
            verdict="reject",
            proposed_decision="trade",
            objections=[_model_objection()],
        ),
        deterministic_objections=[],
    )

    assert effect.final_decision == "watchlist"
    assert "critic_reject_invalid_proposed_decision" in effect.reason_codes


def _request(*, max_confidence_penalty: float = 0.30) -> CritiqueRequest:
    return CritiqueRequest(
        schema_version="1.0",
        run_id="run-1",
        context_hash="ctx-1",
        ticker="NVDA",
        as_of_date=date(2026, 9, 27),
        snapshot_at=datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
        draft=_baseline(),
        analyst_views=[],
        evidence_chunks=[],
        deterministic_warnings=[],
        allowed_actions=[
            "accept",
            "downgrade_confidence",
            "downgrade_decision",
            "force_no_trade",
            "request_revision",
        ],
        max_confidence_penalty=max_confidence_penalty,
    )


def _response(
    *,
    decision: str = "trade",
    confidence: float = 0.72,
) -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision=decision,
        confidence=confidence,
        reasons=["draft"],
        warnings=[],
        engine_version="v1.rules.0",
        trace_id="trace-1",
        entry_range=(100.0, 101.0),
        stop_loss=95.0,
        take_profit=(108.0, 110.0),
        risk_reward=2.0,
        position_size_eur=1000.0,
    )


def _baseline(
    *,
    decision: str = "trade",
    confidence: float = 0.72,
) -> DraftRecommendation:
    return DraftRecommendation(
        decision=decision,
        confidence=confidence,
        reasons=["baseline"],
        warnings=[],
        entry_range=(100.0, 101.0),
        stop_loss=95.0,
        take_profit=(108.0, 110.0),
        risk_reward=2.0,
        position_size_eur=1000.0,
        memo="",
        source="deterministic",
    )


def _critique_result(
    *,
    accepted: bool = True,
    verdict: str = "accept",
    proposed_decision: str | None = None,
    penalty: float = 0.0,
    reason_codes: list[str] | None = None,
    objections: list[CritiqueObjection] | None = None,
    revision_instructions: str = "",
) -> CritiqueResult:
    response = CritiqueResponse.model_construct(
        schema_version="1.0",
        run_id="run-1",
        context_hash="ctx-1",
        agent_name="critic_guardrail",
        status="completed",
        verdict=verdict,
        objections=objections or [],
        proposed_decision=proposed_decision,
        proposed_confidence_penalty=penalty,
        revision_instructions=revision_instructions,
        summary="Critic summary.",
    )
    return CritiqueResult(
        response=response,
        validation=CritiqueValidation(accepted=accepted, reason_codes=reason_codes or []),
        raw_output="{}",
        latency_ms=12.0,
        token_usage={"total_token_count": 5},
        model_name="fake-critic",
        prompt_version="critic-guardrail.v1",
    )


def _deterministic_objection(
    category: str,
    severity: str,
    *,
    objection_id: str = "det:1",
    warning_codes: list[str] | None = None,
) -> CritiqueObjection:
    return CritiqueObjection.model_construct(
        objection_id=objection_id,
        category=category,
        severity=severity,
        source="deterministic",
        claim=f"{category}:{severity}",
        metric_ids=["metric-1"] if severity in {"medium", "high"} else [],
        chunk_ids=[],
        warning_codes=warning_codes or ["warning-1"],
    )


def _model_objection() -> CritiqueObjection:
    return CritiqueObjection.model_construct(
        objection_id="model:1",
        category="unsupported_claim",
        severity="high",
        source="model",
        claim="Unsupported claim.",
        metric_ids=["metric-1"],
        chunk_ids=[],
        warning_codes=["warning-1"],
    )
