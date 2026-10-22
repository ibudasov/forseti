from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from agents.orchestration.registry import CRITIC_NAME, SPECIALIST_ORDER
from app.schemas.analyze import AnalysisTrace, AnalyzeResponse
from app.services.decision_diagnosis import MISSING_DATA_WARNINGS

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
    violations.extend(check_technical_agent_effect_rank(agentic_response))
    return violations


def check_technical_agent_effect_rank(agentic_response: AnalyzeResponse) -> list[InvariantViolation]:
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


CRITIC_OFF_ALLOWED_SKIPS = {CRITIC_NAME: {"critic_mode_off"}}
