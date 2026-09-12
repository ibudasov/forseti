from __future__ import annotations

from typing import Literal

from app.schemas.analyze import AnalyzeResponse
from app.schemas.fundamentals import (
    FundamentalAgentEffect,
    FundamentalAnalysisRequest,
    FundamentalAssessmentResult,
)
from app.services.analyzer import SCORE_TRADE_MIN, SCORE_WATCHLIST_MIN

HARD_BLOCKER_WARNINGS = {
    "security_inactive",
    "no_price_data",
    "insufficient_price_data",
    "stale_price_data",
    "no_fundamentals",
}


def apply_fundamental_policy(
    *,
    mode: str,
    deterministic_response: AnalyzeResponse,
    request: FundamentalAnalysisRequest,
    assessment_result: FundamentalAssessmentResult,
) -> FundamentalAgentEffect:
    baseline_total_score = (
        deterministic_response.diagnosis.checklist_score
        if deterministic_response.diagnosis and deterministic_response.diagnosis.checklist_score is not None
        else 0
    )
    baseline_fundamental_score = request.deterministic_result.score
    raw_adjustment = assessment_result.response.proposed_score_adjustment
    reason_codes = list(assessment_result.validation.reason_codes)
    accepted = assessment_result.validation.accepted
    hard_blocker = _has_hard_blocker(deterministic_response)
    applied_adjustment = 0

    if not accepted:
        reason_codes.append("assessment_invalid")
    elif _has_duplicate_citations(assessment_result.response):
        accepted = False
        reason_codes.extend(["duplicate_evidence_detected", "assessment_invalid"])
    elif raw_adjustment == 0:
        reason_codes.append("assessment_neutral")
    elif hard_blocker and raw_adjustment > 0:
        reason_codes.append("hard_blocker_prevents_promotion")
    else:
        applied_adjustment = raw_adjustment
        reason_codes.append("adjustment_applied")

    adjusted_fundamental_score = _clamp_score(
        baseline_fundamental_score + applied_adjustment,
        request.deterministic_result.maximum_score,
    )
    adjusted_total_score = max(0, baseline_total_score + applied_adjustment)
    counterfactual_decision = _counterfactual_decision(
        baseline_decision=deterministic_response.decision,
        adjusted_total_score=adjusted_total_score,
        hard_blocker=hard_blocker,
    )
    final_decision = deterministic_response.decision
    if mode == "shadow":
        reason_codes.append("shadow_mode")
    elif mode == "enforced" and accepted:
        final_decision = counterfactual_decision
        if final_decision != deterministic_response.decision:
            reason_codes.append("enforced_mode")

    return FundamentalAgentEffect(
        mode=mode,
        accepted=accepted,
        reason_codes=_deduplicated(reason_codes),
        raw_adjustment=raw_adjustment,
        applied_adjustment=applied_adjustment,
        baseline_fundamental_score=baseline_fundamental_score,
        adjusted_fundamental_score=adjusted_fundamental_score,
        baseline_total_score=baseline_total_score,
        adjusted_total_score=adjusted_total_score,
        baseline_decision=deterministic_response.decision,
        counterfactual_decision=counterfactual_decision,
        final_decision=final_decision,
        decision_changed=final_decision != deterministic_response.decision,
    )


def _has_hard_blocker(response: AnalyzeResponse) -> bool:
    diagnosis = response.diagnosis
    if diagnosis is not None and diagnosis.stage in {"unknown_security", "data_gate", "hard_veto", "risk_math"}:
        return True
    return any(warning in HARD_BLOCKER_WARNINGS for warning in response.warnings)


def _has_duplicate_citations(response) -> bool:
    seen_signatures: set[tuple[tuple[str, ...], tuple[int, ...]]] = set()
    for finding in [*response.findings, *response.contradictions, *response.material_red_flags]:
        signature = (tuple(sorted(finding.metric_ids)), tuple(sorted(finding.chunk_ids)))
        if signature == ((), ()):
            continue
        if signature in seen_signatures:
            return True
        seen_signatures.add(signature)
    return False


def _counterfactual_decision(
    *,
    baseline_decision: Literal["trade", "watchlist", "no_trade"],
    adjusted_total_score: int,
    hard_blocker: bool,
) -> Literal["trade", "watchlist", "no_trade"]:
    if hard_blocker:
        return baseline_decision
    if adjusted_total_score >= SCORE_TRADE_MIN:
        return "trade"
    if adjusted_total_score >= SCORE_WATCHLIST_MIN:
        return "watchlist"
    return "no_trade"


def _clamp_score(score: int, maximum_score: int) -> int:
    return max(0, min(maximum_score, score))


def _deduplicated(reason_codes: list[str]) -> list[str]:
    return list(dict.fromkeys(reason_codes))
