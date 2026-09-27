from __future__ import annotations

import math
from typing import Literal

from app.schemas.critique import AnalystView, CritiqueObjection, CritiqueRequest, DraftRecommendation
from app.schemas.fundamentals import CitedFinding
from app.services.fundamental_policy import HARD_BLOCKER_WARNINGS

RULES_VERSION = "1.0"
FLOAT_ABSOLUTE_TOLERANCE = 1e-6
_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}
_SIGNAL_POLARITY = {
    "strong_negative": -1,
    "negative": -1,
    "neutral": 0,
    "positive": 1,
    "strong_positive": 1,
}
_RISK_FIELDS = (
    "entry_range",
    "stop_loss",
    "take_profit",
    "risk_reward",
    "position_size_eur",
)
_REQUIRED_TRADE_FIELDS = _RISK_FIELDS
_Decision = Literal["trade", "watchlist", "no_trade"]
_ObjectionCategory = Literal[
    "analyst_contradiction",
    "unsupported_claim",
    "stale_or_incomplete_data",
    "risk_number_mismatch",
    "internal_inconsistency",
]
_ObjectionSeverity = Literal["low", "medium", "high"]
_Signal = Literal["strong_negative", "negative", "neutral", "positive", "strong_positive"]


def evaluate_deterministic_objections(
    *,
    request: CritiqueRequest,
    baseline: DraftRecommendation,
) -> list[CritiqueObjection]:
    evaluators = (
        _risk_number_mismatch_objections,
        _decision_upgrade_objections,
        _stale_or_incomplete_data_objections,
        _analyst_contradiction_objections,
        _missing_trade_field_objections,
        _unsupported_claim_objections,
    )
    objections: list[CritiqueObjection] = []
    for evaluator in evaluators:
        objections.extend(evaluator(request=request, baseline=baseline))
    return objections


def _risk_number_mismatch_objections(
    *,
    request: CritiqueRequest,
    baseline: DraftRecommendation,
) -> list[CritiqueObjection]:
    mismatched_fields = [
        field_name
        for field_name in _RISK_FIELDS
        if not _risk_values_match(
            _field_value(request.draft, field_name),
            _field_value(baseline, field_name),
        )
    ]
    return [
        _objection(
            objection_id=f"det:risk_number_mismatch:{index}",
            category="risk_number_mismatch",
            severity="high",
            claim=f"Draft field '{field_name}' differs from the deterministic baseline.",
            warning_codes=[f"risk_number_mismatch:{field_name}"],
        )
        for index, field_name in enumerate(mismatched_fields, start=1)
    ]


def _decision_upgrade_objections(
    *,
    request: CritiqueRequest,
    baseline: DraftRecommendation,
) -> list[CritiqueObjection]:
    if _decision_rank(request.draft.decision) <= _decision_rank(baseline.decision):
        return []
    return [
        _objection(
            objection_id="det:decision_upgrade:1",
            category="internal_inconsistency",
            severity="high",
            claim=(
                f"Draft decision '{request.draft.decision}' upgrades deterministic "
                f"baseline '{baseline.decision}'."
            ),
            warning_codes=[f"decision_upgrade:{baseline.decision}:{request.draft.decision}"],
        )
    ]


def _stale_or_incomplete_data_objections(
    *,
    request: CritiqueRequest,
    baseline: DraftRecommendation,
) -> list[CritiqueObjection]:
    del baseline
    blocker_warnings = [
        warning for warning in request.deterministic_warnings if warning in HARD_BLOCKER_WARNINGS
    ]
    return [
        _objection(
            objection_id=f"det:stale_or_incomplete_data:{index}",
            category="stale_or_incomplete_data",
            severity="high",
            claim=f"Deterministic warning '{warning}' blocks the draft recommendation.",
            warning_codes=[warning],
        )
        for index, warning in enumerate(blocker_warnings, start=1)
    ]


def _analyst_contradiction_objections(
    *,
    request: CritiqueRequest,
    baseline: DraftRecommendation,
) -> list[CritiqueObjection]:
    del baseline
    objections = _negative_fundamental_trade_objections(request)
    return objections + _opposite_polarity_objections(
        request,
        start_index=len(objections) + 1,
    )


def _missing_trade_field_objections(
    *,
    request: CritiqueRequest,
    baseline: DraftRecommendation,
) -> list[CritiqueObjection]:
    del baseline
    if request.draft.decision != "trade":
        return []
    missing_fields = [
        field_name
        for field_name in _REQUIRED_TRADE_FIELDS
        if _field_value(request.draft, field_name) is None
    ]
    return [
        _objection(
            objection_id=f"det:missing_trade_field:{index}",
            category="internal_inconsistency",
            severity="high",
            claim=f"Trade draft is missing required field '{field_name}'.",
            warning_codes=[f"missing_risk_field:{field_name}"],
        )
        for index, field_name in enumerate(missing_fields, start=1)
    ]


def _unsupported_claim_objections(
    *,
    request: CritiqueRequest,
    baseline: DraftRecommendation,
) -> list[CritiqueObjection]:
    del baseline
    findings = _unsupported_findings(request.analyst_views)
    return [
        _objection(
            objection_id=f"det:unsupported_claim:{index}",
            category="unsupported_claim",
            severity="medium",
            claim=f"Finding '{finding.finding_id}' in {view.agent_name} lacks required citations.",
            warning_codes=[f"unsupported_claim:{view.agent_name}:{finding.finding_id}"],
        )
        for index, (view, finding) in enumerate(findings, start=1)
    ]


def _negative_fundamental_trade_objections(request: CritiqueRequest) -> list[CritiqueObjection]:
    view = _completed_view(request.analyst_views, "fundamental_analyst")
    if (
        view is None
        or request.draft.decision != "trade"
        or view.overall_signal not in {"negative", "strong_negative"}
    ):
        return []
    metric_ids, chunk_ids, warning_codes = _view_references(
        [view],
        f"analyst_contradiction:{view.agent_name}:{view.overall_signal}",
    )
    return [
        _objection(
            objection_id="det:analyst_contradiction:1",
            category="analyst_contradiction",
            severity="high",
            claim="Completed fundamental analysis contradicts a trade decision.",
            metric_ids=metric_ids,
            chunk_ids=chunk_ids,
            warning_codes=warning_codes,
        )
    ]


def _opposite_polarity_objections(
    request: CritiqueRequest,
    *,
    start_index: int,
) -> list[CritiqueObjection]:
    completed_views = [view for view in request.analyst_views if view.status == "completed"]
    if len(completed_views) < 2 or not _views_have_opposite_polarity(
        completed_views[0],
        completed_views[1],
    ):
        return []
    metric_ids, chunk_ids, warning_codes = _view_references(
        completed_views,
        (
            "analyst_contradiction:"
            f"{completed_views[0].agent_name}:{completed_views[0].overall_signal}:"
            f"{completed_views[1].agent_name}:{completed_views[1].overall_signal}"
        ),
    )
    return [
        _objection(
            objection_id=f"det:analyst_contradiction:{start_index}",
            category="analyst_contradiction",
            severity="medium",
            claim="Completed analyst views have opposite directional signals.",
            metric_ids=metric_ids,
            chunk_ids=chunk_ids,
            warning_codes=warning_codes,
        )
    ]


def _unsupported_findings(views: list[AnalystView]) -> list[tuple[AnalystView, CitedFinding]]:
    findings: list[tuple[AnalystView, CitedFinding]] = []
    for view in views:
        if view.status != "completed":
            continue
        for finding in view.findings:
            if finding.materiality in {"medium", "high"} and not (finding.metric_ids or finding.chunk_ids):
                findings.append((view, finding))
    return findings


def _view_references(
    views: list[AnalystView],
    fallback_warning_code: str,
) -> tuple[list[str], list[int], list[str]]:
    metric_ids = _unique_strings(
        metric_id
        for view in views
        for finding in view.findings
        for metric_id in finding.metric_ids
    )
    chunk_ids = _unique_ints(
        chunk_id
        for view in views
        for finding in view.findings
        for chunk_id in finding.chunk_ids
    )
    if metric_ids or chunk_ids:
        return metric_ids, chunk_ids, []
    return [], [], [fallback_warning_code]


def _completed_view(
    views: list[AnalystView],
    agent_name: Literal["fundamental_analyst", "technical_analyst"],
) -> AnalystView | None:
    for view in views:
        if view.agent_name == agent_name and view.status == "completed":
            return view
    return None


def _views_have_opposite_polarity(first: AnalystView, second: AnalystView) -> bool:
    return _signal_polarity(first.overall_signal) * _signal_polarity(second.overall_signal) < 0


def _signal_polarity(signal: _Signal) -> int:
    return _SIGNAL_POLARITY[signal]


def _decision_rank(decision: _Decision) -> int:
    return _DECISION_RANK[decision]


def _risk_values_match(left, right) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(
            _numbers_match(left_value, right_value)
            for left_value, right_value in zip(left, right)
        )
    return _numbers_match(left, right)


def _numbers_match(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=FLOAT_ABSOLUTE_TOLERANCE)


def _field_value(recommendation: DraftRecommendation, field_name: str):
    return getattr(recommendation, field_name)


def _unique_strings(values) -> list[str]:
    return list(dict.fromkeys(values))


def _unique_ints(values) -> list[int]:
    return list(dict.fromkeys(values))


def _objection(
    *,
    objection_id: str,
    category: _ObjectionCategory,
    severity: _ObjectionSeverity,
    claim: str,
    metric_ids: list[str] | None = None,
    chunk_ids: list[int] | None = None,
    warning_codes: list[str] | None = None,
) -> CritiqueObjection:
    return CritiqueObjection(
        objection_id=objection_id,
        category=category,
        severity=severity,
        source="deterministic",
        claim=claim,
        metric_ids=metric_ids or [],
        chunk_ids=chunk_ids or [],
        warning_codes=warning_codes or [],
    )
