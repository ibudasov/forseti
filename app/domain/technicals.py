"""Deterministic technical baseline contract.

Mirrors ``app.domain.fundamentals``: a pure, versioned object that records
what the technical checklist rules (5-9) concluded, independent of any future
Technical Analyst policy logic. See ``docs/technical-analysis-contracts.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import PriceBar, TechnicalFeature

SCHEMA_VERSION: Literal["1.0"] = "1.0"
TECHNICAL_MAXIMUM_SCORE: Literal[5] = 5

TechnicalUnit = Literal["price", "ratio", "index_points", "rsi_points"]

PRICE_UNIT: TechnicalUnit = "price"
RATIO_UNIT: TechnicalUnit = "ratio"
INDEX_POINTS_UNIT: TechnicalUnit = "index_points"
RSI_POINTS_UNIT: TechnicalUnit = "rsi_points"

RSI_HEALTHY_MIN = Decimal("45")
RSI_HEALTHY_MAX = Decimal("65")
VOLUME_TREND_MIN = Decimal("1.0")
VIX_CALM_MAX = Decimal("25")

MetricName = Literal["close", "sma_50", "sma_200", "rsi_14", "volume_trend", "vix_close"]
RuleId = Literal["close_vs_sma50", "close_vs_sma200", "rsi_healthy", "volume_trend", "vix_calm"]


@dataclass(frozen=True)
class TechnicalMetricDefinition:
    metric_name: MetricName
    unit: TechnicalUnit


TECHNICAL_METRIC_DEFINITIONS: dict[MetricName, TechnicalMetricDefinition] = {
    "close": TechnicalMetricDefinition(metric_name="close", unit=PRICE_UNIT),
    "sma_50": TechnicalMetricDefinition(metric_name="sma_50", unit=PRICE_UNIT),
    "sma_200": TechnicalMetricDefinition(metric_name="sma_200", unit=PRICE_UNIT),
    "rsi_14": TechnicalMetricDefinition(metric_name="rsi_14", unit=RSI_POINTS_UNIT),
    "volume_trend": TechnicalMetricDefinition(metric_name="volume_trend", unit=RATIO_UNIT),
    "vix_close": TechnicalMetricDefinition(metric_name="vix_close", unit=INDEX_POINTS_UNIT),
}


class TechnicalMetricRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    metric_name: MetricName
    value: Decimal | None
    unit: Literal["price", "ratio", "index_points", "rsi_points"]
    as_of_date: date | None


class TechnicalRuleResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: RuleId
    status: Literal["passed", "failed", "unknown"]
    points_awarded: int = Field(ge=0)
    points_available: int = Field(ge=0)
    metric_ids: list[str] = Field(default_factory=list)
    explanation: str

    @model_validator(mode="after")
    def _validate_points(self) -> "TechnicalRuleResult":
        if self.status == "passed" and self.points_awarded != self.points_available:
            raise ValueError("Passed rules must award all available points.")
        if self.status in {"failed", "unknown"} and self.points_awarded != 0:
            raise ValueError("Failed and unknown rules must award zero points.")
        return self


class TechnicalVetoState(BaseModel):
    """Informational mirror of an analyzer veto. Never re-decides vetoes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    veto_id: str
    triggered: bool
    metric_ids: list[str] = Field(default_factory=list)


class DeterministicTechnicalAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    ticker: str
    as_of_date: date | None
    score: int = Field(ge=0)
    maximum_score: Literal[5] = TECHNICAL_MAXIMUM_SCORE
    rule_results: list[TechnicalRuleResult] = Field(default_factory=list)
    veto_states: list[TechnicalVetoState] = Field(default_factory=list)
    metrics: list[TechnicalMetricRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_consistency(self) -> "DeterministicTechnicalAnalysis":
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


def analyze_technicals(
    latest_bar: Optional[PriceBar],
    technical_feature: Optional[TechnicalFeature],
    vix_close: Optional[Decimal],
    *,
    ticker: str,
    veto_states: Sequence[TechnicalVetoState] = (),
) -> DeterministicTechnicalAnalysis:
    """Evaluate the deterministic technical baseline (checklist rules 5-9).

    Pure function: no DB access, no network calls, no clock reads. Callers
    pass in already-loaded rows and an already-fetched VIX close.
    """
    as_of_date = _as_of_date(latest_bar, technical_feature)
    metrics = _build_metric_refs(latest_bar, technical_feature, vix_close, as_of_date)
    metrics_by_name = {metric.metric_name: metric for metric in metrics}

    rule_results = [
        _evaluate_close_vs_sma50(metrics_by_name.get("close"), metrics_by_name.get("sma_50")),
        _evaluate_close_vs_sma200(metrics_by_name.get("close"), metrics_by_name.get("sma_200")),
        _evaluate_rsi_healthy(metrics_by_name.get("rsi_14")),
        _evaluate_volume_trend(metrics_by_name.get("volume_trend")),
        _evaluate_vix_calm(metrics_by_name.get("vix_close")),
    ]
    warnings = _build_warnings(technical_feature, vix_close, rule_results)
    score = sum(result.points_awarded for result in rule_results)
    return DeterministicTechnicalAnalysis(
        ticker=ticker,
        as_of_date=as_of_date,
        score=score,
        rule_results=rule_results,
        veto_states=list(veto_states),
        metrics=metrics,
        warnings=warnings,
    )


def _as_of_date(
    latest_bar: Optional[PriceBar],
    technical_feature: Optional[TechnicalFeature],
) -> date | None:
    if technical_feature is not None:
        return technical_feature.as_of_date
    if latest_bar is not None:
        return latest_bar.bar_date
    return None


def _metric_id(metric_name: MetricName, as_of_date: date | None) -> str:
    date_label = as_of_date.isoformat() if as_of_date is not None else "unknown"
    return f"{metric_name}:{date_label}"


def _build_metric_ref(
    metric_name: MetricName,
    value: Optional[Decimal],
    as_of_date: date | None,
) -> TechnicalMetricRef:
    definition = TECHNICAL_METRIC_DEFINITIONS[metric_name]
    return TechnicalMetricRef(
        metric_id=_metric_id(metric_name, as_of_date),
        metric_name=metric_name,
        value=value,
        unit=definition.unit,
        as_of_date=as_of_date,
    )


def _build_metric_refs(
    latest_bar: Optional[PriceBar],
    technical_feature: Optional[TechnicalFeature],
    vix_close: Optional[Decimal],
    as_of_date: date | None,
) -> list[TechnicalMetricRef]:
    refs: list[TechnicalMetricRef] = []
    if latest_bar is not None:
        refs.append(_build_metric_ref("close", Decimal(str(latest_bar.close)), latest_bar.bar_date))
    if technical_feature is not None:
        refs.append(_build_metric_ref("sma_50", technical_feature.sma_50, technical_feature.as_of_date))
        refs.append(_build_metric_ref("sma_200", technical_feature.sma_200, technical_feature.as_of_date))
        refs.append(_build_metric_ref("rsi_14", technical_feature.rsi_14, technical_feature.as_of_date))
        refs.append(
            _build_metric_ref("volume_trend", technical_feature.volume_trend, technical_feature.as_of_date)
        )
    if vix_close is not None:
        refs.append(_build_metric_ref("vix_close", vix_close, as_of_date))
    return refs


def _unknown_result(rule_id: RuleId, points_available: int = 1) -> TechnicalRuleResult:
    return TechnicalRuleResult(
        rule_id=rule_id,
        status="unknown",
        points_awarded=0,
        points_available=points_available,
        metric_ids=[],
        explanation=f"{rule_id}: missing",
    )


def _evaluate_close_vs_sma50(
    close_metric: TechnicalMetricRef | None,
    sma_metric: TechnicalMetricRef | None,
) -> TechnicalRuleResult:
    return _evaluate_close_vs_sma(
        rule_id="close_vs_sma50",
        close_metric=close_metric,
        sma_metric=sma_metric,
        sma_label="sma_50",
    )


def _evaluate_close_vs_sma200(
    close_metric: TechnicalMetricRef | None,
    sma_metric: TechnicalMetricRef | None,
) -> TechnicalRuleResult:
    return _evaluate_close_vs_sma(
        rule_id="close_vs_sma200",
        close_metric=close_metric,
        sma_metric=sma_metric,
        sma_label="sma_200",
    )


def _evaluate_close_vs_sma(
    *,
    rule_id: RuleId,
    close_metric: TechnicalMetricRef | None,
    sma_metric: TechnicalMetricRef | None,
    sma_label: str,
) -> TechnicalRuleResult:
    if close_metric is None or sma_metric is None:
        return _unknown_result(rule_id)
    if close_metric.value is None or sma_metric.value is None:
        return _unknown_result(rule_id)

    close = close_metric.value
    sma = sma_metric.value
    metric_ids = [close_metric.metric_id, sma_metric.metric_id]
    if close > sma:
        return TechnicalRuleResult(
            rule_id=rule_id,
            status="passed",
            points_awarded=1,
            points_available=1,
            metric_ids=metric_ids,
            explanation=f"close: {float(close):.2f} > {sma_label}: {float(sma):.2f}",
        )
    return TechnicalRuleResult(
        rule_id=rule_id,
        status="failed",
        points_awarded=0,
        points_available=1,
        metric_ids=metric_ids,
        explanation=f"close: {float(close):.2f} <= {sma_label}: {float(sma):.2f}",
    )


def _evaluate_rsi_healthy(rsi_metric: TechnicalMetricRef | None) -> TechnicalRuleResult:
    rule_id: RuleId = "rsi_healthy"
    if rsi_metric is None or rsi_metric.value is None:
        return _unknown_result(rule_id)

    rsi = rsi_metric.value
    metric_ids = [rsi_metric.metric_id]
    if RSI_HEALTHY_MIN <= rsi <= RSI_HEALTHY_MAX:
        return TechnicalRuleResult(
            rule_id=rule_id,
            status="passed",
            points_awarded=1,
            points_available=1,
            metric_ids=metric_ids,
            explanation=f"rsi_14: {float(rsi):.2f} in [{RSI_HEALTHY_MIN}, {RSI_HEALTHY_MAX}]",
        )
    return TechnicalRuleResult(
        rule_id=rule_id,
        status="failed",
        points_awarded=0,
        points_available=1,
        metric_ids=metric_ids,
        explanation=f"rsi_14: {float(rsi):.2f} outside [{RSI_HEALTHY_MIN}, {RSI_HEALTHY_MAX}]",
    )


def _evaluate_volume_trend(volume_trend_metric: TechnicalMetricRef | None) -> TechnicalRuleResult:
    rule_id: RuleId = "volume_trend"
    if volume_trend_metric is None or volume_trend_metric.value is None:
        return _unknown_result(rule_id)

    volume_trend = volume_trend_metric.value
    metric_ids = [volume_trend_metric.metric_id]
    if volume_trend > VOLUME_TREND_MIN:
        return TechnicalRuleResult(
            rule_id=rule_id,
            status="passed",
            points_awarded=1,
            points_available=1,
            metric_ids=metric_ids,
            explanation=f"volume_trend: {float(volume_trend):.4f} > 1.0",
        )
    return TechnicalRuleResult(
        rule_id=rule_id,
        status="failed",
        points_awarded=0,
        points_available=1,
        metric_ids=metric_ids,
        explanation=f"volume_trend: {float(volume_trend):.4f} <= 1.0",
    )


def _evaluate_vix_calm(vix_metric: TechnicalMetricRef | None) -> TechnicalRuleResult:
    rule_id: RuleId = "vix_calm"
    if vix_metric is None or vix_metric.value is None:
        return _unknown_result(rule_id)

    vix = vix_metric.value
    metric_ids = [vix_metric.metric_id]
    if vix < VIX_CALM_MAX:
        return TechnicalRuleResult(
            rule_id=rule_id,
            status="passed",
            points_awarded=1,
            points_available=1,
            metric_ids=metric_ids,
            explanation=f"vix: {float(vix):.2f} < {VIX_CALM_MAX} calm threshold",
        )
    return TechnicalRuleResult(
        rule_id=rule_id,
        status="failed",
        points_awarded=0,
        points_available=1,
        metric_ids=metric_ids,
        explanation=f"vix: {float(vix):.2f} >= {VIX_CALM_MAX} calm threshold",
    )


def _build_warnings(
    technical_feature: Optional[TechnicalFeature],
    vix_close: Optional[Decimal],
    rule_results: list[TechnicalRuleResult],
) -> list[str]:
    warnings: list[str] = []
    if technical_feature is None:
        warnings.append("no_technical_features")
    if vix_close is None:
        warnings.append("no_vix")
    for result in rule_results:
        if result.status == "unknown":
            warnings.append(f"missing_metric:{result.rule_id}")
    return warnings
