from __future__ import annotations

from typing import Iterable

from app.services.checklist import MAX_SCORE

MISSING_DATA_WARNINGS = (
    "no_technical_features",
    "no_fundamentals",
    "no_earnings_data",
    "no_price_data",
    "insufficient_price_data",
    "stale_price_data",
)

_MISSING_DATA_NAMES = {
    "no_technical_features": "technical_features",
    "no_fundamentals": "fundamentals",
    "no_earnings_data": "earnings",
    "no_price_data": "price_data",
    "insufficient_price_data": "price_data",
    "stale_price_data": "price_data",
}


def _debug_reason(stage: str, rule_id: str, detail: str) -> str:
    single_line_detail = " ".join(detail.split())
    reason = f"{stage}/{rule_id}: {single_line_detail}"
    return reason[:200]


def _build(stage: str, rule_id: str, detail: str, **kwargs):
    from app.schemas.analyze import DecisionDiagnosis

    normalized_detail = " ".join(detail.split())[:200]
    return DecisionDiagnosis(
        stage=stage,
        rule_id=rule_id,
        detail=normalized_detail,
        debug_reason=_debug_reason(stage, rule_id, normalized_detail),
        **kwargs,
    )


def _missing_data(warnings: Iterable[str]) -> list[str]:
    return [_MISSING_DATA_NAMES[warning] for warning in MISSING_DATA_WARNINGS if warning in warnings]


def from_unknown_security(symbol: str):
    return _build("unknown_security", "ticker_not_found", f"security not found: {symbol}")


def from_data_gate(gate_reasons: Iterable[str], warnings: Iterable[str]):
    reason = next(iter(gate_reasons), "data_gate: data requirements not met")
    rule_id, _, detail = reason.partition(":")
    return _build(
        "data_gate",
        rule_id,
        detail.strip() or reason,
        missing_data=_missing_data(warnings),
    )


def from_veto(veto):
    return _build("hard_veto", veto.rule_id, veto.detail)


def from_checklist(score: int, checklist_results, warnings: Iterable[str]):
    if score < 5:
        rule_id = "score_below_watchlist"
    elif score < 8:
        rule_id = "score_below_trade"
    else:
        rule_id = "checklist_passed"
    missing = _missing_data(warnings)
    missing_text = ", ".join(missing) if missing else "none"
    detail = f"score {score}/{MAX_SCORE}, missing: {missing_text}"
    return _build(
        "checklist",
        rule_id,
        detail,
        checklist_score=score,
        missing_data=missing,
    )


def from_risk_downgrade(risk_downgrade):
    return _build("risk_math", risk_downgrade.reason, risk_downgrade.detail)
