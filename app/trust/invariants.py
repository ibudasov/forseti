from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from agents.orchestration.registry import (
    CRITIC_NAME,
    DECISION_SYNTHESIZER_NAME,
    FUNDAMENTAL_ANALYST_NAME,
    SPECIALIST_ORDER,
    TECHNICAL_ANALYST_NAME,
)
from app.schemas.analyze import AnalysisTrace, AnalyzeResponse
from app.services.decision_diagnosis import MISSING_DATA_WARNINGS

if TYPE_CHECKING:
    # Deferred to a function-local import at runtime (see
    # `check_technical_numeric_claims_grounded`): `app.services.technical_analyst`
    # imports `app.trust.memo_checks`, which would otherwise create an
    # import-time cycle back into this module through `app.trust.__init__`.
    from app.schemas.technicals import TechnicalAnalysisRequest, TechnicalAssessmentResponse

RISK_FIELDS = (
    "entry_range",
    "stop_loss",
    "take_profit",
    "position_size_eur",
    "risk_reward",
)
DATA_GATE_WARNINGS = {"security_inactive", *MISSING_DATA_WARNINGS}
DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}


@dataclass(frozen=True)
class InvariantViolation:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_specialist_sequence(
    trace: AnalysisTrace,
    *,
    allowed_skip_reasons: Mapping[str, set[str]] | None = None,
) -> list[InvariantViolation]:
    allowed_skip_reasons = allowed_skip_reasons or {}
    violations: list[InvariantViolation] = []
    first_steps_by_agent = {}
    for step in trace.steps:
        if step.agent_name not in SPECIALIST_ORDER or step.agent_name in first_steps_by_agent:
            continue
        first_steps_by_agent[step.agent_name] = step

    missing_agents = [agent_name for agent_name in SPECIALIST_ORDER if agent_name not in first_steps_by_agent]
    for agent_name in missing_agents:
        violations.append(
            InvariantViolation(
                code="missing_specialist_step",
                message=f"Trace is missing specialist step '{agent_name}'.",
                details={"agent_name": agent_name},
            )
        )

    if missing_agents:
        return violations

    sequences = [first_steps_by_agent[agent_name].sequence for agent_name in SPECIALIST_ORDER]
    if sequences != sorted(sequences):
        violations.append(
            InvariantViolation(
                code="specialist_order_mismatch",
                message="Specialist steps do not appear in SPECIALIST_ORDER.",
                details={
                    "expected_order": list(SPECIALIST_ORDER),
                    "actual_order": sorted(
                        first_steps_by_agent,
                        key=lambda agent_name: first_steps_by_agent[agent_name].sequence,
                    ),
                },
            )
        )

    for agent_name in SPECIALIST_ORDER:
        step = first_steps_by_agent[agent_name]
        if step.status == "skipped":
            allowed = allowed_skip_reasons.get(agent_name, set())
            skip_reason = step.output.get("reason") if isinstance(step.output, dict) else step.skip_reason
            if skip_reason not in allowed:
                violations.append(
                    InvariantViolation(
                        code="specialist_skipped",
                        message=f"Specialist step '{agent_name}' was skipped.",
                        details={"agent_name": agent_name, "skip_reason": skip_reason},
                    )
                )
            continue
        if step.status == "failed":
            violations.append(
                InvariantViolation(
                    code="specialist_failed",
                    message=f"Specialist step '{agent_name}' failed.",
                    details={"agent_name": agent_name},
                )
            )

    return violations


def check_risk_field_equality(
    deterministic_response: AnalyzeResponse,
    agentic_response: AnalyzeResponse,
) -> list[InvariantViolation]:
    violations: list[InvariantViolation] = []
    for field_name in RISK_FIELDS:
        deterministic_value = getattr(deterministic_response, field_name)
        agentic_value = getattr(agentic_response, field_name)
        if agentic_value != deterministic_value:
            violations.append(
                InvariantViolation(
                    code="risk_field_mismatch",
                    message=f"Agentic field '{field_name}' differs from deterministic output.",
                    details={
                        "field_name": field_name,
                        "expected": deterministic_value,
                        "actual": agentic_value,
                    },
                )
            )
    return violations


def check_downgrade_only_behavior(
    deterministic_response: AnalyzeResponse,
    agentic_response: AnalyzeResponse,
) -> list[InvariantViolation]:
    violations: list[InvariantViolation] = []
    if DECISION_RANK[agentic_response.decision] > DECISION_RANK[deterministic_response.decision]:
        violations.append(
            InvariantViolation(
                code="decision_upgrade",
                message=(
                    f"Agentic decision '{agentic_response.decision}' upgrades "
                    f"deterministic decision '{deterministic_response.decision}'."
                ),
                details={
                    "expected_maximum": deterministic_response.decision,
                    "actual": agentic_response.decision,
                },
            )
        )
    if agentic_response.confidence > deterministic_response.confidence:
        violations.append(
            InvariantViolation(
                code="confidence_upgrade",
                message=(
                    f"Agentic confidence {agentic_response.confidence} exceeds deterministic "
                    f"confidence {deterministic_response.confidence}."
                ),
                details={
                    "expected_maximum": deterministic_response.confidence,
                    "actual": agentic_response.confidence,
                },
            )
        )
    return violations


def check_data_gate_warning_propagation(
    deterministic_response: AnalyzeResponse,
    agentic_response: AnalyzeResponse,
) -> list[InvariantViolation]:
    baseline_warnings = {warning for warning in deterministic_response.warnings if warning in DATA_GATE_WARNINGS}
    agentic_warnings = set(agentic_response.warnings)
    missing_warnings = sorted(baseline_warnings - agentic_warnings)
    if not missing_warnings:
        return []
    return [
        InvariantViolation(
            code="missing_data_gate_warning",
            message="Agentic response dropped one or more deterministic data-gate warnings.",
            details={"missing_warnings": missing_warnings},
        )
    ]


def check_critic_shadow_persistence(
    agentic_response: AnalyzeResponse,
    trace: AnalysisTrace,
) -> list[InvariantViolation]:
    violations: list[InvariantViolation] = []
    response_effect = agentic_response.critic_effect
    persisted_effect = trace.critic_effect

    if response_effect is None:
        violations.append(
            InvariantViolation(
                code="missing_critic_effect",
                message="Shadow-mode response is missing critic_effect.",
            )
        )
        return violations

    if response_effect.mode != "shadow":
        violations.append(
            InvariantViolation(
                code="critic_mode_mismatch",
                message="Shadow-mode response critic_effect.mode must be 'shadow'.",
                details={"actual_mode": response_effect.mode},
            )
        )

    if persisted_effect is None:
        violations.append(
            InvariantViolation(
                code="missing_persisted_critic_effect",
                message="Persisted trace is missing critic_effect for a shadow-mode run.",
            )
        )
    elif persisted_effect != response_effect:
        violations.append(
            InvariantViolation(
                code="persisted_critic_effect_mismatch",
                message="Persisted trace critic_effect does not match the response critic_effect.",
            )
        )

    if response_effect.revisions_requested > 1 or response_effect.revisions_performed > 1:
        violations.append(
            InvariantViolation(
                code="critic_revision_limit_exceeded",
                message="Critic requested or performed more than one revision.",
                details={
                    "revisions_requested": response_effect.revisions_requested,
                    "revisions_performed": response_effect.revisions_performed,
                },
            )
        )

    critic_steps = [step for step in trace.steps if step.agent_name == CRITIC_NAME]
    if len(critic_steps) != 1:
        violations.append(
            InvariantViolation(
                code="critic_step_count_mismatch",
                message="Expected exactly one critic_guardrail step in the persisted trace.",
                details={"critic_step_count": len(critic_steps)},
            )
        )

    return violations


def evaluate_agentic_invariants(
    *,
    deterministic_response: AnalyzeResponse,
    agentic_response: AnalyzeResponse,
    trace: AnalysisTrace,
    allowed_skip_reasons: Mapping[str, set[str]] | None = None,
) -> list[InvariantViolation]:
    violations: list[InvariantViolation] = []
    violations.extend(
        check_specialist_sequence(
            trace,
            allowed_skip_reasons=allowed_skip_reasons,
        )
    )
    violations.extend(check_risk_field_equality(deterministic_response, agentic_response))
    violations.extend(check_downgrade_only_behavior(deterministic_response, agentic_response))
    violations.extend(check_data_gate_warning_propagation(deterministic_response, agentic_response))
    violations.extend(check_fundamental_agent_authority(agentic_response))
    violations.extend(check_technical_agent_effect_rank(agentic_response))
    violations.extend(check_technical_risk_fields_untouched_in_shadow(agentic_response))
    violations.extend(check_fundamental_step_zero_tokens_in_typed_mode(trace))
    violations.extend(check_technical_step_zero_tokens_in_typed_mode(trace))
    violations.extend(check_synthesizer_step_zero_tokens_in_typed_mode(trace))
    violations.extend(
        check_synthesizer_never_changes_risk_numbers(deterministic_response, agentic_response, trace)
    )
    return violations


def check_fundamental_agent_authority(agentic_response: AnalyzeResponse) -> list[InvariantViolation]:
    """ADR 008: even shadow counterfactuals must respect the deterministic ceiling."""
    effect = agentic_response.fundamental_agent_effect
    if effect is None:
        return []
    violations: list[InvariantViolation] = []
    for field_name in ("counterfactual_decision", "final_decision"):
        decision = getattr(effect, field_name)
        if DECISION_RANK[decision] <= DECISION_RANK[effect.baseline_decision]:
            continue
        violations.append(
            InvariantViolation(
                code="fundamental_agent_effect_rank_upgrade",
                message=f"fundamental_agent_effect.{field_name} ranks above its baseline.",
                details={"field": field_name, "baseline_decision": effect.baseline_decision, "actual": decision},
            )
        )
    if effect.applied_adjustment > 0:
        violations.append(
            InvariantViolation(
                code="fundamental_positive_adjustment",
                message="Fundamental adjustments must be downgrade-only.",
                details={"applied_adjustment": effect.applied_adjustment},
            )
        )
    return violations


def check_technical_agent_effect_rank(agentic_response: AnalyzeResponse) -> list[InvariantViolation]:
    """TA-7 invariant `technical_never_upgrades`: `rank(final) <= rank(baseline)`.

    The Technical Analyst's policy is downgrade-only by construction (see
    ``app.services.technical_policy``), so a persisted effect that ranks the
    final decision above its own baseline can only mean a future regression
    in that policy; it must never reach a caller.
    """
    effect = agentic_response.technical_agent_effect
    if effect is None:
        return []
    if DECISION_RANK[effect.final_decision] <= DECISION_RANK[effect.baseline_decision]:
        return []
    return [
        InvariantViolation(
            code="technical_agent_effect_rank_upgrade",
            message=(
                f"technical_agent_effect.final_decision '{effect.final_decision}' ranks above "
                f"baseline_decision '{effect.baseline_decision}'."
            ),
            details={
                "baseline_decision": effect.baseline_decision,
                "final_decision": effect.final_decision,
            },
        )
    ]


def check_technical_risk_fields_untouched_in_shadow(
    agentic_response: AnalyzeResponse,
) -> list[InvariantViolation]:
    """TA-7 invariant `technical_risk_fields_untouched_in_shadow`.

    In `shadow` mode the Technical Analyst may only *observe*: it computes a
    `counterfactual_decision` for later analysis, but the decision, risk
    fields, and confidence actually returned to the caller must stay exactly
    whatever the deterministic/fundamental stages already produced. This
    re-checks the persisted `TechnicalAgentEffect` fields directly (rather
    than only comparing response-level numbers), so it also catches a
    regression in `apply_technical_policy` itself, not just in how its
    output happens to be applied to the response.
    """
    effect = agentic_response.technical_agent_effect
    if effect is None or effect.mode != "shadow":
        return []

    violations: list[InvariantViolation] = []
    if effect.final_decision != effect.baseline_decision:
        violations.append(
            InvariantViolation(
                code="technical_shadow_decision_mutated",
                message=(
                    f"Shadow-mode final_decision '{effect.final_decision}' differs from "
                    f"baseline_decision '{effect.baseline_decision}'."
                ),
                details={
                    "baseline_decision": effect.baseline_decision,
                    "final_decision": effect.final_decision,
                },
            )
        )
    if effect.decision_changed:
        violations.append(
            InvariantViolation(
                code="technical_shadow_decision_changed_flag_set",
                message="Shadow-mode technical_agent_effect.decision_changed must be False.",
            )
        )
    if effect.final_confidence != effect.baseline_confidence:
        violations.append(
            InvariantViolation(
                code="technical_shadow_confidence_mutated",
                message=(
                    f"Shadow-mode final_confidence {effect.final_confidence} differs from "
                    f"baseline_confidence {effect.baseline_confidence}."
                ),
                details={
                    "baseline_confidence": effect.baseline_confidence,
                    "final_confidence": effect.final_confidence,
                },
            )
        )
    return violations


def check_technical_numeric_claims_grounded(
    response: TechnicalAssessmentResponse,
    request: TechnicalAnalysisRequest,
) -> list[InvariantViolation]:
    """TA-7 invariant `technical_numeric_claims_grounded`.

    Re-runs the TA-3 numeric-claim check (`validate_technical_assessment`)
    over a persisted assessment response/request pair, and reports a
    violation only for the specific `uncited_numeric_claim` reason code --
    the other reason codes `validate_technical_assessment` can return
    (identity, bounds, metric-citation existence, negative-adjustment
    support) are already covered by `TechnicalAssessmentResult.validation`
    and the `apply_technical_policy` acceptance gate, so re-asserting them
    here would duplicate, not extend, existing coverage.
    """
    from app.services.technical_analyst import validate_technical_assessment

    validation = validate_technical_assessment(response, request)
    if "uncited_numeric_claim" not in validation.reason_codes:
        return []
    return [
        InvariantViolation(
            code="technical_numeric_claim_ungrounded",
            message=(
                f"Technical assessment run_id={response.run_id} cites a numeric claim that does "
                "not match any known fact or metric value."
            ),
            details={"run_id": response.run_id, "context_hash": response.context_hash},
        )
    ]


def check_technical_step_zero_tokens_in_typed_mode(trace: AnalysisTrace) -> list[InvariantViolation]:
    """TA-7 invariant `technical_step_zero_tokens_in_typed_mode`.

    The ADK step for `technical_analyst` is the typed view from TA-5
    (`agents.orchestration.typed_view_agent.TypedTechnicalViewAgent`): it
    only serialises the already-computed `TechnicalAssessmentResult` into
    session state and must never make a model call. Any persisted
    `technical_analyst` trace step whose output carries
    `source: "typed_assessment"` must therefore report zero tokens across
    every usage field, or a future refactor could silently start
    double-billing (and double-latency-ing) the same assessment.
    """
    violations: list[InvariantViolation] = []
    for step in trace.steps:
        if step.agent_name != TECHNICAL_ANALYST_NAME or not isinstance(step.output, dict):
            continue
        if step.output.get("source") != "typed_assessment":
            continue
        nonzero_token_usage = {key: value for key, value in step.token_usage.items() if value}
        if nonzero_token_usage:
            violations.append(
                InvariantViolation(
                    code="technical_typed_view_step_nonzero_tokens",
                    message="Typed-view technical_analyst trace step must report zero tokens.",
                    details={"sequence": step.sequence, "token_usage": nonzero_token_usage},
                )
            )
    return violations


def check_fundamental_step_zero_tokens_in_typed_mode(trace: AnalysisTrace) -> list[InvariantViolation]:
    """Require the typed Fundamental Analyst view event to have zero token usage."""
    violations: list[InvariantViolation] = []
    for step in trace.steps:
        if step.agent_name != FUNDAMENTAL_ANALYST_NAME or not isinstance(step.output, dict):
            continue
        if step.output.get("source") != "typed_assessment":
            continue
        nonzero_token_usage = {key: value for key, value in step.token_usage.items() if value}
        if nonzero_token_usage:
            violations.append(
                InvariantViolation(
                    code="fundamental_typed_view_step_nonzero_tokens",
                    message="Typed-view fundamental_analyst trace step must report zero tokens.",
                    details={"sequence": step.sequence, "token_usage": nonzero_token_usage},
                )
            )
    return violations


def check_synthesizer_step_zero_tokens_in_typed_mode(trace: AnalysisTrace) -> list[InvariantViolation]:
    """LE-5 invariant `synthesizer_step_zero_tokens_in_typed_mode`.

    In typed mode the Decision Synthesizer runs outside ADK, so its ADK step
    only renders the validated memo and must never make a model call. The
    service's own token usage is accounted in the run total instead.
    """
    violations: list[InvariantViolation] = []
    for step in trace.steps:
        if step.agent_name != DECISION_SYNTHESIZER_NAME or not isinstance(step.output, dict):
            continue
        if step.output.get("source") != "typed_synthesis":
            continue
        nonzero_token_usage = {key: value for key, value in step.token_usage.items() if value}
        if nonzero_token_usage:
            violations.append(
                InvariantViolation(
                    code="synthesizer_typed_step_nonzero_tokens",
                    message="Typed-mode decision_synthesizer trace step must report zero tokens.",
                    details={"sequence": step.sequence, "token_usage": nonzero_token_usage},
                )
            )
    return violations


def check_synthesizer_never_changes_risk_numbers(
    deterministic_response: AnalyzeResponse,
    agentic_response: AnalyzeResponse,
    trace: AnalysisTrace,
) -> list[InvariantViolation]:
    """LE-5 invariant `synthesizer_never_changes_risk_numbers`.

    Applies to runs with a typed synthesizer effect: the response risk fields
    must equal the deterministic ones and the rendered memo risk block must
    carry the deterministic numbers verbatim.
    """
    if trace.synthesizer_effect is None:
        return []
    violations = [
        InvariantViolation(
            code="synthesizer_changed_risk_number",
            message=f"Typed synthesizer run changed risk field '{violation.details['field_name']}'.",
            details=violation.details,
        )
        for violation in check_risk_field_equality(deterministic_response, agentic_response)
    ]
    memo = agentic_response.memo
    if memo is None:
        return violations
    for field_name in RISK_FIELDS:
        for number in _risk_numbers(getattr(deterministic_response, field_name)):
            if f"{number:g}" in memo.risk_block:
                continue
            violations.append(
                InvariantViolation(
                    code="memo_risk_block_mismatch",
                    message=f"Memo risk block does not carry deterministic '{field_name}'.",
                    details={"field_name": field_name, "expected": number},
                )
            )
    return violations


def _risk_numbers(value: Any) -> tuple[float, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    return (value,)


CRITIC_OFF_ALLOWED_SKIPS = {CRITIC_NAME: {"critic_mode_off"}}
