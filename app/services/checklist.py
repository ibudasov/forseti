from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from app.db.models import Fundamental, PriceBar, TechnicalFeature
from app.domain.fundamentals import (
    FundamentalSnapshotData,
    analyze_fundamentals,
)

# Frozen constants for checklist scoring
RSI_HEALTHY_MIN = 45
RSI_HEALTHY_MAX = 65
VIX_CALM_MAX = 25
MAX_SCORE = 11


@dataclass
class ChecklistResult:
    """Result of a single checklist rule."""
    rule_id: str
    points: int
    detail: str


def evaluate_checklist(
    latest_bar: Optional[PriceBar],
    fundamental: Optional[Fundamental],
    technical_feature: Optional[TechnicalFeature],
    vix_close: Optional[Decimal],
    ticker: str = "",
    currency: Optional[str] = None,
) -> tuple[int, List[ChecklistResult]]:
    """
    Evaluate all checklist rules.
    Returns tuple of (total_score, results_list).
    Null inputs score 0 for that rule.
    """
    results: List[ChecklistResult] = []
    total_score = 0

    # Rules 1-4: Deterministic fundamentals baseline (+6 max)
    fundamental_analysis = analyze_fundamentals(
        ticker=ticker,
        snapshot=_to_fundamental_snapshot(fundamental, currency),
    )
    for rule_result in fundamental_analysis.rule_results:
        if rule_result.status != "passed":
            continue
        result = ChecklistResult(
            rule_id=rule_result.rule_id,
            points=rule_result.points_awarded,
            detail=rule_result.explanation,
        )
        results.append(result)
        total_score += result.points

    # Rule 5: Close vs SMA50 (+1)
    result = _check_close_vs_sma50(latest_bar, technical_feature)
    if result:
        results.append(result)
        total_score += result.points

    # Rule 6: Close vs SMA200 (+1)
    result = _check_close_vs_sma200(latest_bar, technical_feature)
    if result:
        results.append(result)
        total_score += result.points

    # Rule 7: RSI healthy 45-65 (+1)
    result = _check_rsi_healthy(technical_feature)
    if result:
        results.append(result)
        total_score += result.points

    # Rule 8: Volume trend > 1.0 (+1)
    result = _check_volume_trend(technical_feature)
    if result:
        results.append(result)
        total_score += result.points

    # Rule 9: VIX calm < 25 (+1)
    result = _check_vix_calm(vix_close)
    if result:
        results.append(result)
        total_score += result.points

    return total_score, results


def _to_fundamental_snapshot(
    fundamental: Optional[Fundamental],
    currency: Optional[str],
) -> FundamentalSnapshotData:
    if fundamental is None:
        return FundamentalSnapshotData(currency=currency)

    return FundamentalSnapshotData(
        as_of_date=fundamental.as_of_date,
        revenue_growth=fundamental.revenue_growth,
        fcf=fundamental.fcf,
        debt_to_equity=fundamental.debt_to_equity,
        eps_trend=fundamental.eps_trend,
        margins=fundamental.margins,
        currency=currency,
    )


def _check_close_vs_sma50(
    latest_bar: Optional[PriceBar],
    technical_feature: Optional[TechnicalFeature],
) -> Optional[ChecklistResult]:
    if latest_bar is None or technical_feature is None or technical_feature.sma_50 is None:
        return None
    close = Decimal(str(latest_bar.close))
    sma_50 = Decimal(str(technical_feature.sma_50))
    if close > sma_50:
        return ChecklistResult(
            rule_id="close_vs_sma50",
            points=1,
            detail=f"close: {float(close):.2f} > sma_50: {float(sma_50):.2f}",
        )
    return None


def _check_close_vs_sma200(
    latest_bar: Optional[PriceBar],
    technical_feature: Optional[TechnicalFeature],
) -> Optional[ChecklistResult]:
    if latest_bar is None or technical_feature is None or technical_feature.sma_200 is None:
        return None
    close = Decimal(str(latest_bar.close))
    sma_200 = Decimal(str(technical_feature.sma_200))
    if close > sma_200:
        return ChecklistResult(
            rule_id="close_vs_sma200",
            points=1,
            detail=f"close: {float(close):.2f} > sma_200: {float(sma_200):.2f}",
        )
    return None


def _check_rsi_healthy(technical_feature: Optional[TechnicalFeature]) -> Optional[ChecklistResult]:
    if technical_feature is None or technical_feature.rsi_14 is None:
        return None
    rsi = Decimal(str(technical_feature.rsi_14))
    if RSI_HEALTHY_MIN <= rsi <= RSI_HEALTHY_MAX:
        return ChecklistResult(
            rule_id="rsi_healthy",
            points=1,
            detail=f"rsi_14: {float(rsi):.2f} in [{RSI_HEALTHY_MIN}, {RSI_HEALTHY_MAX}]",
        )
    return None


def _check_volume_trend(technical_feature: Optional[TechnicalFeature]) -> Optional[ChecklistResult]:
    if technical_feature is None or technical_feature.volume_trend is None:
        return None
    vt = Decimal(str(technical_feature.volume_trend))
    if vt > Decimal("1.0"):
        return ChecklistResult(
            rule_id="volume_trend",
            points=1,
            detail=f"volume_trend: {float(vt):.4f} > 1.0",
        )
    return None


def _check_vix_calm(vix_close: Optional[Decimal]) -> Optional[ChecklistResult]:
    if vix_close is None:
        return None
    vix = Decimal(str(vix_close))
    if vix < VIX_CALM_MAX:
        return ChecklistResult(
            rule_id="vix_calm",
            points=1,
            detail=f"vix: {float(vix):.2f} < {VIX_CALM_MAX} calm threshold",
        )
    return None
