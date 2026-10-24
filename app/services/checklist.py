from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from app.db.models import Fundamental, PriceBar, TechnicalFeature
from app.domain.fundamentals import (
    FundamentalSnapshotData,
    analyze_fundamentals,
)
from app.domain.technicals import (
    RSI_HEALTHY_MAX as _RSI_HEALTHY_MAX,
    RSI_HEALTHY_MIN as _RSI_HEALTHY_MIN,
    VIX_CALM_MAX as _VIX_CALM_MAX,
    analyze_technicals,
)

# Frozen constants for checklist scoring. These mirror app.domain.technicals;
# other modules import the int-typed aliases below for veto comparisons.
RSI_HEALTHY_MIN = int(_RSI_HEALTHY_MIN)
RSI_HEALTHY_MAX = int(_RSI_HEALTHY_MAX)
VIX_CALM_MAX = int(_VIX_CALM_MAX)
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

    # Rules 5-9: Deterministic technical baseline (+5 max)
    technical_analysis = analyze_technicals(
        latest_bar,
        technical_feature,
        vix_close,
        ticker="UNKNOWN",
    )
    for rule_result in technical_analysis.rule_results:
        if rule_result.status != "passed":
            continue
        result = ChecklistResult(
            rule_id=rule_result.rule_id,
            points=rule_result.points_awarded,
            detail=rule_result.explanation,
        )
        results.append(result)
        total_score += result.points

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
