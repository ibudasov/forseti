from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"
MAX_FUNDAMENTAL_SCORE = 6
RATIO_UNIT = "ratio"
CURRENCY_PER_SHARE_DELTA_UNIT = "currency_per_share_delta"

REVENUE_GROWTH_MIN = Decimal("0.15")
FCF_MIN = Decimal("0")
DEBT_TO_EQUITY_MAX = Decimal("1.0")
EPS_TREND_MIN = Decimal("0")


@dataclass(frozen=True)
class FundamentalMetricDefinition:
    name: str
    unit: str | None


FUNDAMENTAL_METRIC_DEFINITIONS = {
    "revenue_growth": FundamentalMetricDefinition(name="Revenue growth", unit=RATIO_UNIT),
    "fcf": FundamentalMetricDefinition(name="Free cash flow", unit=None),
    "debt_to_equity": FundamentalMetricDefinition(name="Debt to equity", unit=RATIO_UNIT),
    "eps_trend": FundamentalMetricDefinition(
        name="EPS trend",
        unit=CURRENCY_PER_SHARE_DELTA_UNIT,
    ),
    "margins": FundamentalMetricDefinition(name="Margins", unit=RATIO_UNIT),
}


class FundamentalSnapshotData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_snapshot: bool = True
    as_of_date: Optional[date] = None
    revenue_growth: Optional[Decimal] = None
    fcf: Optional[Decimal] = None
    debt_to_equity: Optional[Decimal] = None
    eps_trend: Optional[Decimal] = None
    margins: Optional[Decimal] = None
    currency: Optional[str] = None


class FundamentalMetricRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str
    name: str
    value: Decimal | None
    unit: str | None
    period_end: date


class FundamentalRuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    status: Literal["passed", "failed", "unknown"]
    points_awarded: int = Field(ge=0)
    points_available: int = Field(ge=0)
    metric_ids: list[str] = Field(default_factory=list)
    explanation: str

    @model_validator(mode="after")
    def _validate_points(self) -> "FundamentalRuleResult":
        if self.status == "passed" and self.points_awarded != self.points_available:
            raise ValueError("Passed rules must award all available points.")
        if self.status in {"failed", "unknown"} and self.points_awarded != 0:
            raise ValueError("Failed and unknown rules must award zero points.")
        return self


class DeterministicFundamentalAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    ticker: str
    as_of_date: date | None
    score: int = Field(ge=0)
    maximum_score: int = Field(ge=0)
    rule_results: list[FundamentalRuleResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: list[FundamentalMetricRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_consistency(self) -> "DeterministicFundamentalAnalysis":
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema version: {self.schema_version}")

        expected_score = sum(result.points_awarded for result in self.rule_results)
        if self.score != expected_score:
            raise ValueError("Score must equal the sum of awarded points.")
        if self.score > self.maximum_score:
            raise ValueError("Score must be within the declared maximum score.")

        metric_values = {metric.metric_id: metric.value for metric in self.metrics}
        for result in self.rule_results:
            if result.status != "passed":
                continue
            missing_metric_ids = [
                metric_id
                for metric_id in result.metric_ids
                if metric_values.get(metric_id) is None
            ]
            if missing_metric_ids:
                raise ValueError(
                    f"Passed rule {result.rule_id} cannot cite null metrics: {missing_metric_ids}"
                )
        return self


def analyze_fundamentals(
    ticker: str,
    snapshot: FundamentalSnapshotData,
) -> DeterministicFundamentalAnalysis:
    metric_values = _metric_values(snapshot)
    metrics = _build_metric_refs(snapshot, metric_values)
    metrics_by_key = {metric.metric_id.split(":")[0]: metric for metric in metrics}
    rule_results = [
        _evaluate_minimum_rule(
            rule_id="revenue_growth",
            metric=metrics_by_key.get("revenue_growth"),
            metric_value=metric_values["revenue_growth"],
            threshold=REVENUE_GROWTH_MIN,
            points_available=2,
            threshold_label=f"{float(REVENUE_GROWTH_MIN):.2f} min",
        ),
        _evaluate_minimum_rule(
            rule_id="fcf",
            metric=metrics_by_key.get("fcf"),
            metric_value=metric_values["fcf"],
            threshold=FCF_MIN,
            points_available=2,
            threshold_label="0",
        ),
        _evaluate_maximum_rule(
            rule_id="debt_to_equity",
            metric=metrics_by_key.get("debt_to_equity"),
            metric_value=metric_values["debt_to_equity"],
            threshold=DEBT_TO_EQUITY_MAX,
            points_available=1,
            threshold_label=f"{float(DEBT_TO_EQUITY_MAX):.2f} max",
        ),
        _evaluate_minimum_rule(
            rule_id="eps_trend",
            metric=metrics_by_key.get("eps_trend"),
            metric_value=metric_values["eps_trend"],
            threshold=EPS_TREND_MIN,
            points_available=1,
            threshold_label=f"{float(EPS_TREND_MIN):.2f} min",
        ),
    ]
    warnings = _build_warnings(snapshot, rule_results)
    score = sum(result.points_awarded for result in rule_results)
    return DeterministicFundamentalAnalysis(
        ticker=ticker,
        as_of_date=snapshot.as_of_date,
        score=score,
        maximum_score=MAX_FUNDAMENTAL_SCORE,
        rule_results=rule_results,
        warnings=warnings,
        metrics=metrics,
    )


def _metric_values(snapshot: FundamentalSnapshotData) -> dict[str, Optional[Decimal]]:
    return {
        "revenue_growth": snapshot.revenue_growth,
        "fcf": snapshot.fcf,
        "debt_to_equity": snapshot.debt_to_equity,
        "eps_trend": snapshot.eps_trend,
        "margins": snapshot.margins,
    }


def _build_metric_refs(
    snapshot: FundamentalSnapshotData,
    metric_values: dict[str, Optional[Decimal]],
) -> list[FundamentalMetricRef]:
    if not snapshot.has_snapshot or snapshot.as_of_date is None:
        return []

    refs: list[FundamentalMetricRef] = []
    for metric_key, value in metric_values.items():
        definition = FUNDAMENTAL_METRIC_DEFINITIONS[metric_key]
        refs.append(
            FundamentalMetricRef(
                metric_id=_metric_id(metric_key, snapshot.as_of_date),
                name=definition.name,
                value=value,
                unit=_metric_unit(metric_key, snapshot.currency),
                period_end=snapshot.as_of_date,
            )
        )
    return refs


def _metric_id(metric_key: str, period_end: date) -> str:
    return f"{metric_key}:{period_end.isoformat()}"


def _metric_unit(metric_key: str, currency: str | None) -> str | None:
    definition = FUNDAMENTAL_METRIC_DEFINITIONS[metric_key]
    if metric_key == "fcf":
        return currency
    return definition.unit


def _evaluate_minimum_rule(
    *,
    rule_id: str,
    metric: FundamentalMetricRef | None,
    metric_value: Decimal | None,
    threshold: Decimal,
    points_available: int,
    threshold_label: str,
) -> FundamentalRuleResult:
    return _evaluate_threshold_rule(
        rule_id=rule_id,
        metric=metric,
        metric_value=metric_value,
        threshold=threshold,
        points_available=points_available,
        threshold_label=threshold_label,
        comparison=">",
    )


def _evaluate_maximum_rule(
    *,
    rule_id: str,
    metric: FundamentalMetricRef | None,
    metric_value: Decimal | None,
    threshold: Decimal,
    points_available: int,
    threshold_label: str,
) -> FundamentalRuleResult:
    return _evaluate_threshold_rule(
        rule_id=rule_id,
        metric=metric,
        metric_value=metric_value,
        threshold=threshold,
        points_available=points_available,
        threshold_label=threshold_label,
        comparison="<",
    )


def _evaluate_threshold_rule(
    *,
    rule_id: str,
    metric: FundamentalMetricRef | None,
    metric_value: Decimal | None,
    threshold: Decimal,
    points_available: int,
    threshold_label: str,
    comparison: Literal[">", "<"],
) -> FundamentalRuleResult:
    if metric is None:
        if metric_value is not None:
            raise ValueError(f"{rule_id} metric reference is missing for a present metric value.")
        return FundamentalRuleResult(
            rule_id=rule_id,
            status="unknown",
            points_awarded=0,
            points_available=points_available,
            metric_ids=[],
            explanation=f"{rule_id}: missing",
        )

    metric_ids = [metric.metric_id]
    if metric.value is None:
        return FundamentalRuleResult(
            rule_id=rule_id,
            status="unknown",
            points_awarded=0,
            points_available=points_available,
            metric_ids=metric_ids,
            explanation=f"{rule_id}: missing",
        )

    if metric_value is None:
        raise ValueError(f"{rule_id} metric value cannot be null when the metric reference has a value.")

    passed = metric_value > threshold if comparison == ">" else metric_value < threshold
    operator = comparison if passed else _inverse_operator(comparison)
    return FundamentalRuleResult(
        rule_id=rule_id,
        status="passed" if passed else "failed",
        points_awarded=points_available if passed else 0,
        points_available=points_available,
        metric_ids=metric_ids,
        explanation=f"{rule_id}: {float(metric_value):.2f} {operator} {threshold_label}",
    )


def _inverse_operator(comparison: Literal[">", "<"]) -> str:
    if comparison == ">":
        return "<="
    return ">="


def _build_warnings(
    snapshot: FundamentalSnapshotData,
    rule_results: list[FundamentalRuleResult],
) -> list[str]:
    warnings: list[str] = []
    if not snapshot.has_snapshot:
        warnings.append("no_fundamental_snapshot")
    for result in rule_results:
        if result.status == "unknown":
            warnings.append(f"missing_metric:{result.rule_id}")
    return warnings
