from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from app.db.models import Fundamental, PriceBar, TechnicalFeature

# Frozen constants for checklist scoring
REVENUE_GROWTH_MIN = Decimal("0.15")
DEBT_TO_EQUITY_MAX = Decimal("1.0")
EPS_TREND_MIN = Decimal("0")
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
) -> tuple[int, List[ChecklistResult]]:
    """
    Evaluate all checklist rules.
    Returns tuple of (total_score, results_list).
    Null inputs score 0 for that rule.
    """
    results: List[ChecklistResult] = []
    total_score = 0

    # Rule 1: Revenue growth > 0.15 YoY (+2)
    result = _check_revenue_growth(fundamental)
    if result:
        results.append(result)
        total_score += result.points

    # Rule 2: FCF > 0 (+2)
    result = _check_fcf(fundamental)
    if result:
        results.append(result)
        total_score += result.points

    # Rule 3: Debt to equity < 1.0 (+1)
    result = _check_debt_to_equity(fundamental)
    if result:
        results.append(result)
        total_score += result.points

    # Rule 4: EPS trend > 0 (+1)
    result = _check_eps_trend(fundamental)
    if result:
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


def _check_revenue_growth(fundamental: Optional[Fundamental]) -> Optional[ChecklistResult]:
    if fundamental is None or fundamental.revenue_growth is None:
        return None
    rg = Decimal(str(fundamental.revenue_growth))
    if rg > REVENUE_GROWTH_MIN:
        return ChecklistResult(
            rule_id="revenue_growth",
            points=2,
            detail=f"revenue_growth: {float(rg):.2f} > {float(REVENUE_GROWTH_MIN):.2f} min",
        )
    return None


def _check_fcf(fundamental: Optional[Fundamental]) -> Optional[ChecklistResult]:
    if fundamental is None or fundamental.fcf is None:
        return None
    fcf = Decimal(str(fundamental.fcf))
    if fcf > 0:
        return ChecklistResult(
            rule_id="fcf",
            points=2,
            detail=f"fcf: {float(fcf):.2f} > 0",
        )
    return None


def _check_debt_to_equity(fundamental: Optional[Fundamental]) -> Optional[ChecklistResult]:
    if fundamental is None or fundamental.debt_to_equity is None:
        return None
    de = Decimal(str(fundamental.debt_to_equity))
    if de < DEBT_TO_EQUITY_MAX:
        return ChecklistResult(
            rule_id="debt_to_equity",
            points=1,
            detail=f"debt_to_equity: {float(de):.2f} < {float(DEBT_TO_EQUITY_MAX):.2f} max",
        )
    return None


def _check_eps_trend(fundamental: Optional[Fundamental]) -> Optional[ChecklistResult]:
    if fundamental is None or fundamental.eps_trend is None:
        return None
    et = Decimal(str(fundamental.eps_trend))
    if et > EPS_TREND_MIN:
        return ChecklistResult(
            rule_id="eps_trend",
            points=1,
            detail=f"eps_trend: {float(et):.2f} > {float(EPS_TREND_MIN):.2f} min",
        )
    return None


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
