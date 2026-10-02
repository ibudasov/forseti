from __future__ import annotations

from collections import Counter
from typing import Literal, Sequence, cast

from app.schemas.analyze import AnalyzeResponse
from app.schemas.critique import (
    CriticEffect,
    CriticVersionStamp,
    CritiqueResult,
    CritiqueObjection,
    CritiqueRequest,
    DraftRecommendation,
)

POLICY_VERSION = "1.0"
ANALYST_CONTRADICTION_CONFIDENCE_PENALTY = 0.15
MEDIUM_OBJECTION_CONFIDENCE_PENALTY = 0.05
_WATCHLIST_DECISION: Literal["watchlist"] = "watchlist"
_NO_TRADE_DECISION: Literal["no_trade"] = "no_trade"
_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}
_Decision = Literal["trade", "watchlist", "no_trade"]


def apply_critic_policy(
    *,
    mode: str,
    response: AnalyzeResponse,
    request: CritiqueRequest,
    baseline: DraftRecommendation,
    critique_result: CritiqueResult,
    deterministic_objections: Sequence[CritiqueObjection],
) -> CriticEffect:
    reason_codes = list(critique_result.validation.reason_codes)
    decision: _Decision = response.decision
    confidence = response.confidence
    requested_penalty = 0.0
    revisions_requested = 0
    deterministic_action = _deterministic_decision_action(deterministic_objections)

    if deterministic_action == "restore_baseline_risk_mismatch":
        decision = baseline.decision
        confidence = baseline.confidence
        reason_codes.append("guardrail_rejected: risk_number_mismatch")
    elif deterministic_action == "restore_baseline_upgrade_attempt":
        decision = baseline.decision
        confidence = baseline.confidence
        reason_codes.append("guardrail_rejected: decision_upgrade_attempt")
    elif deterministic_action == "force_no_trade":
        decision = _NO_TRADE_DECISION
        reason_codes.append("guardrail_forced_no_trade")
    elif deterministic_action == "cap_watchlist":
        decision = _capped_decision(decision, _WATCHLIST_DECISION)
        reason_codes.append("guardrail_capped_watchlist")
    elif deterministic_action == "analyst_contradiction":
        decision = _capped_decision(decision, _WATCHLIST_DECISION)
        requested_penalty += ANALYST_CONTRADICTION_CONFIDENCE_PENALTY
        reason_codes.append("guardrail_analyst_contradiction")

    medium_penalty = _medium_penalty(deterministic_objections)
    if medium_penalty > 0:
        requested_penalty += medium_penalty
        reason_codes.append("guardrail_confidence_penalty")

    if not critique_result.validation.accepted:
        reason_codes.append("assessment_invalid")
    elif mode != "off":
        decision, requested_penalty, revisions_requested, reason_codes = _apply_model_verdict(
            critique_result=critique_result,
            current_decision=decision,
            requested_penalty=requested_penalty,
            max_confidence_penalty=request.max_confidence_penalty,
            reason_codes=reason_codes,
        )

    applied_penalty = min(requested_penalty, request.max_confidence_penalty)
    final_confidence = _clamped_confidence(confidence - applied_penalty)

    return CriticEffect(
        mode=mode,
        accepted=critique_result.validation.accepted,
        status=critique_result.response.status,
        verdict=critique_result.response.verdict,
        reason_codes=_deduplicated(reason_codes),
        objection_counts=_objection_counts(
            [*deterministic_objections, *critique_result.response.objections]
        ),
        deterministic_objection_ids=[objection.objection_id for objection in deterministic_objections],
        model_objection_ids=[
            objection.objection_id
            for objection in critique_result.response.objections
            if objection.source == "model"
        ],
        baseline_decision=baseline.decision,
        proposed_decision=critique_result.response.proposed_decision,
        final_decision=decision,
        decision_changed=decision != baseline.decision,
        baseline_confidence=baseline.confidence,
        applied_confidence_penalty=applied_penalty,
        final_confidence=final_confidence,
        revisions_requested=revisions_requested,
        revisions_performed=0,
        versions=CriticVersionStamp(
            request_schema_version=request.schema_version,
            critique_schema_version=critique_result.response.schema_version,
            prompt_version=critique_result.prompt_version,
            model_name=critique_result.model_name,
            policy_version=POLICY_VERSION,
        ),
    )


def _apply_model_verdict(
    *,
    critique_result: CritiqueResult,
    current_decision: _Decision,
    requested_penalty: float,
    max_confidence_penalty: float,
    reason_codes: list[str],
) -> tuple[_Decision, float, int, list[str]]:
    verdict = critique_result.response.verdict
    proposed_decision = critique_result.response.proposed_decision
    revisions_requested = 0
    updated_decision = cast(_Decision, current_decision)
    updated_penalty = requested_penalty + min(
        critique_result.response.proposed_confidence_penalty,
        max_confidence_penalty,
    )

    if verdict == "revise":
        revisions_requested = 1
        reason_codes.append("critic_requested_revision")
        return updated_decision, updated_penalty, revisions_requested, reason_codes

    if verdict != "reject":
        return updated_decision, updated_penalty, revisions_requested, reason_codes

    if proposed_decision is None:
        reason_codes.append("critic_reject_forced_no_trade")
        return _NO_TRADE_DECISION, updated_penalty, revisions_requested, reason_codes

    if _is_strict_downgrade(proposed_decision, current_decision):
        reason_codes.append("critic_reject_applied")
        return proposed_decision, updated_penalty, revisions_requested, reason_codes

    reason_codes.append("critic_reject_invalid_proposed_decision")
    return updated_decision, updated_penalty, revisions_requested, reason_codes


def _deterministic_decision_action(
    objections: Sequence[CritiqueObjection],
) -> Literal[
    "restore_baseline_risk_mismatch",
    "restore_baseline_upgrade_attempt",
    "force_no_trade",
    "cap_watchlist",
    "analyst_contradiction",
] | None:
    if _has_high_category(objections, "risk_number_mismatch"):
        return "restore_baseline_risk_mismatch"
    if _has_decision_upgrade_objection(objections):
        return "restore_baseline_upgrade_attempt"
    if _has_missing_trade_field_objection(objections):
        return "force_no_trade"
    if _has_high_category(objections, "stale_or_incomplete_data"):
        return "cap_watchlist"
    if _has_high_category(objections, "analyst_contradiction"):
        return "analyst_contradiction"
    return None


def _has_high_category(
    objections: Sequence[CritiqueObjection],
    category: str,
) -> bool:
    return any(
        objection.category == category and objection.severity == "high"
        for objection in objections
    )


def _has_decision_upgrade_objection(objections: Sequence[CritiqueObjection]) -> bool:
    return any(
        objection.category == "internal_inconsistency"
        and objection.severity == "high"
        and any(code.startswith("decision_upgrade:") for code in objection.warning_codes)
        for objection in objections
    )


def _has_missing_trade_field_objection(objections: Sequence[CritiqueObjection]) -> bool:
    return any(
        objection.category == "internal_inconsistency"
        and objection.severity == "high"
        and any(code.startswith("missing_risk_field:") for code in objection.warning_codes)
        for objection in objections
    )


def _medium_penalty(objections: Sequence[CritiqueObjection]) -> float:
    medium_count = sum(1 for objection in objections if objection.severity == "medium")
    return medium_count * MEDIUM_OBJECTION_CONFIDENCE_PENALTY


def _is_strict_downgrade(proposed: str, baseline: str) -> bool:
    return _DECISION_RANK[proposed] < _DECISION_RANK[baseline]


def _capped_decision(current: _Decision, cap: _Decision) -> _Decision:
    return current if _DECISION_RANK[current] <= _DECISION_RANK[cap] else cap


def _clamped_confidence(confidence: float) -> float:
    return max(0.0, min(1.0, confidence))


def _objection_counts(objections: Sequence[CritiqueObjection]) -> dict[str, int]:
    return dict(Counter(objection.category for objection in objections))


def _deduplicated(reason_codes: list[str]) -> list[str]:
    return list(dict.fromkeys(reason_codes))
