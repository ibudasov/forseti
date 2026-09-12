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
_MONETARY_UNIT_TAGS_BY_METRIC = {
    "fcf": (
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ),
    "revenue_growth": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
    "debt_to_equity": (
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "DebtCurrent",
        "LongTermDebtCurrent",
        "Liabilities",
        "StockholdersEquity",
    ),
    "eps_trend": (),
    "margins": (
        "NetIncomeLoss",
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
}


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

    # Rules 1-4: Deterministic fundamentals baseline (+6 max)
    fundamental_analysis = analyze_fundamentals(
        ticker="UNKNOWN",
        snapshot=to_fundamental_snapshot(fundamental),
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
    technical_result = _check_close_vs_sma50(latest_bar, technical_feature)
    if technical_result:
        results.append(technical_result)
        total_score += technical_result.points

    # Rule 6: Close vs SMA200 (+1)
    technical_result = _check_close_vs_sma200(latest_bar, technical_feature)
    if technical_result:
        results.append(technical_result)
        total_score += technical_result.points

    # Rule 7: RSI healthy 45-65 (+1)
    technical_result = _check_rsi_healthy(technical_feature)
    if technical_result:
        results.append(technical_result)
        total_score += technical_result.points

    # Rule 8: Volume trend > 1.0 (+1)
    technical_result = _check_volume_trend(technical_feature)
    if technical_result:
        results.append(technical_result)
        total_score += technical_result.points

    # Rule 9: VIX calm < 25 (+1)
    technical_result = _check_vix_calm(vix_close)
    if technical_result:
        results.append(technical_result)
        total_score += technical_result.points

    return total_score, results


def to_fundamental_snapshot(
    fundamental: Optional[Fundamental],
) -> FundamentalSnapshotData:
    if fundamental is None:
        return FundamentalSnapshotData(has_snapshot=False)

    return FundamentalSnapshotData(
        has_snapshot=fundamental.as_of_date is not None,
        as_of_date=fundamental.as_of_date,
        revenue_growth=fundamental.revenue_growth,
        fcf=fundamental.fcf,
        debt_to_equity=fundamental.debt_to_equity,
        eps_trend=fundamental.eps_trend,
        margins=fundamental.margins,
        currency=_fundamental_currency(fundamental),
    )


def _fundamental_currency(fundamental: Fundamental) -> str | None:
    us_gaap = fundamental.raw_payload.get("facts", {}).get("us-gaap", {})
    projected_metric_names = _projected_metric_names(fundamental)
    for metric_name in projected_metric_names:
        for tag_name in _MONETARY_UNIT_TAGS_BY_METRIC[metric_name]:
            fact_payload = us_gaap.get(tag_name, {})
            for unit_name, unit_rows in fact_payload.get("units", {}).items():
                if "/" in unit_name or unit_name == "shares":
                    continue
                if _unit_rows_cover_snapshot_period(unit_rows, fundamental.as_of_date):
                    return unit_name
    return None


def _projected_metric_names(fundamental: Fundamental) -> list[str]:
    projected_metrics: list[str] = []
    if fundamental.fcf is not None:
        projected_metrics.append("fcf")
    if fundamental.revenue_growth is not None:
        projected_metrics.append("revenue_growth")
    if fundamental.debt_to_equity is not None:
        projected_metrics.append("debt_to_equity")
    if fundamental.margins is not None:
        projected_metrics.append("margins")
    if fundamental.eps_trend is not None:
        projected_metrics.append("eps_trend")
    return projected_metrics


def _unit_rows_cover_snapshot_period(unit_rows: list[dict], as_of_date) -> bool:
    for row in unit_rows:
        if row.get("end") == as_of_date.isoformat():
            return True
    return False


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
