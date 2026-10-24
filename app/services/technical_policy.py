"""Downgrade-only policy for the Technical Analyst's proposed adjustments.

Mirrors ``app.services.fundamental_policy`` with one structural difference:
the Technical Analyst runs *after* the fundamental policy in the composition
order (deterministic -> fundamental -> technical -> critic), so its baseline
is whatever decision/score the fundamental stage already produced, not the
raw deterministic output. Because technical adjustments are always <= 0
(enforced by ``TechnicalAssessmentResponse`` validation), the counterfactual
decision is additionally clamped so it can never outrank that post-fundamental
baseline, even if the input scores are inconsistent with it (for example a
risk-math downgrade that leaves the checklist score untouched).
"""

from __future__ import annotations

from typing import Literal, cast

from app.schemas.analyze import AnalyzeResponse
from app.schemas.technicals import (
    TechnicalAgentEffect,
    TechnicalAgentVersionStamp,
    TechnicalAnalysisRequest,
    TechnicalAssessmentResult,
)
from app.services.analyzer import _calculate_confidence
from app.services.fundamental_policy import _clamp_score, _counterfactual_decision, _has_hard_blocker

POLICY_VERSION = "1.0"

_Decision = Literal["trade", "watchlist", "no_trade"]
_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}


def apply_technical_policy(
    *,
    mode: str,
    deterministic_response: AnalyzeResponse,
    post_fundamental_response: AnalyzeResponse,
    request: TechnicalAnalysisRequest,
    assessment_result: TechnicalAssessmentResult,
) -> TechnicalAgentEffect:
    reason_codes = list(assessment_result.validation.reason_codes)
    accepted = assessment_result.validation.accepted
    raw_adjustment = assessment_result.response.proposed_score_adjustment
    applied_adjustment = _resolve_applied_adjustment(accepted, raw_adjustment, reason_codes)

    baseline_technical_score = request.deterministic_result.score
    adjusted_technical_score = _clamp_score(
        baseline_technical_score + applied_adjustment,
        request.deterministic_result.maximum_score,
    )
    baseline_total_score = _baseline_total_score(post_fundamental_response)
    adjusted_total_score = max(0, baseline_total_score + applied_adjustment)

    baseline_decision = cast(_Decision, post_fundamental_response.decision)
    baseline_confidence = post_fundamental_response.confidence
    hard_blocker = _has_hard_blocker(deterministic_response)
    if hard_blocker:
        reason_codes.append("hard_blocker_present")

    counterfactual_decision = _downgrade_only_decision(
        baseline_decision,
        _counterfactual_decision(
            baseline_decision=baseline_decision,
            adjusted_total_score=adjusted_total_score,
            hard_blocker=hard_blocker,
        ),
    )

    final_decision = baseline_decision
    final_confidence = baseline_confidence
    if mode == "shadow":
        reason_codes.append("shadow_mode")
    elif mode == "enforced" and accepted:
        final_decision = counterfactual_decision
        if final_decision != baseline_decision:
            reason_codes.append("enforced_mode")
            final_confidence = min(
                baseline_confidence,
                float(_calculate_confidence(adjusted_total_score, list(post_fundamental_response.warnings))),
            )

    return TechnicalAgentEffect(
        mode=mode,
        accepted=accepted,
        assessment_status=assessment_result.response.status,
        overall_signal=assessment_result.response.overall_signal,
        trend_state=assessment_result.response.trend_state,
        momentum_state=assessment_result.response.momentum_state,
        reason_codes=_deduplicated(reason_codes),
        missing_information=list(assessment_result.response.missing_information),
        raw_adjustment=raw_adjustment,
        applied_adjustment=applied_adjustment,
        baseline_technical_score=baseline_technical_score,
        adjusted_technical_score=adjusted_technical_score,
        baseline_total_score=baseline_total_score,
        adjusted_total_score=adjusted_total_score,
        baseline_decision=baseline_decision,
        counterfactual_decision=counterfactual_decision,
        final_decision=final_decision,
        decision_changed=final_decision != baseline_decision,
        baseline_confidence=baseline_confidence,
        final_confidence=final_confidence,
        versions=TechnicalAgentVersionStamp(
            context_schema_version=request.schema_version,
            assessment_schema_version=assessment_result.response.schema_version,
            prompt_version=assessment_result.prompt_version,
            model_name=assessment_result.model_name,
            policy_version=POLICY_VERSION,
        ),
    )


def _resolve_applied_adjustment(accepted: bool, raw_adjustment: int, reason_codes: list[str]) -> int:
    if not accepted:
        reason_codes.append("assessment_invalid")
        return 0
    if raw_adjustment == 0:
        reason_codes.append("assessment_neutral")
        return 0
    if raw_adjustment > 0:
        # Unreachable through validated model output (adjustments are
        # downgrade-only), but clamped defensively rather than trusted.
        reason_codes.append("positive_adjustment_rejected")
        return 0
    reason_codes.append("adjustment_applied")
    return raw_adjustment


def _baseline_total_score(post_fundamental_response: AnalyzeResponse) -> int:
    fundamental_effect = post_fundamental_response.fundamental_agent_effect
    if fundamental_effect is not None:
        return fundamental_effect.adjusted_total_score
    diagnosis = post_fundamental_response.diagnosis
    if diagnosis is not None and diagnosis.checklist_score is not None:
        return diagnosis.checklist_score
    return 0


def _downgrade_only_decision(baseline_decision: _Decision, proposed_decision: _Decision) -> _Decision:
    if _DECISION_RANK[proposed_decision] > _DECISION_RANK[baseline_decision]:
        return baseline_decision
    return proposed_decision


def _deduplicated(reason_codes: list[str]) -> list[str]:
    return list(dict.fromkeys(reason_codes))
