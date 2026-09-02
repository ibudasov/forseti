"""Turn a :class:`ScreeningResponse` into a small, comparable set of product metrics.

`build_scorecard` is a pure function: it only reads an already-computed
`ScreeningResponse`. It performs no database access and no I/O, which is what
makes it trivially unit-testable against synthetic responses.

Metric direction (whether a bigger or smaller number is "better") lives in the
module-level `_METRIC_DIRECTIONS` table below rather than in conditional logic
scattered through `compare`. Warning counts are always `LOWER_IS_BETTER` and are
looked up by the `warning:<name>` key convention used throughout this module.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence

from app.schemas.screening import ScreeningResponse

WARNING_METRIC_PREFIX = "warning:"


class MetricDirection(str, Enum):
    """Whether a metric improves by going down, up, or is not gated at all."""

    LOWER_IS_BETTER = "lower_is_better"
    HIGHER_IS_BETTER = "higher_is_better"
    NEUTRAL = "neutral"


# Direction for every scalar metric on `Scorecard`. `analyzed_count` is
# HIGHER_IS_BETTER because, for a fixed universe, more successfully analyzed
# tickers means fewer crashes. `universe_size`, `trade_count`, `watchlist_count`
# and `no_trade_count` are NEUTRAL: the trade/watchlist/no_trade split is
# expected to shift legitimately as data sources improve (see forseti#25), so a
# regression gate must never fire on those.
_METRIC_DIRECTIONS: Mapping[str, MetricDirection] = {
    "universe_size": MetricDirection.NEUTRAL,
    "analyzed_count": MetricDirection.HIGHER_IS_BETTER,
    "failed_count": MetricDirection.LOWER_IS_BETTER,
    "trade_count": MetricDirection.NEUTRAL,
    "watchlist_count": MetricDirection.NEUTRAL,
    "no_trade_count": MetricDirection.NEUTRAL,
    "zero_confidence_count": MetricDirection.LOWER_IS_BETTER,
}


def metric_direction(metric_name: str) -> MetricDirection:
    """Return the direction for a scalar metric name or a `warning:<name>` key."""
    if metric_name in _METRIC_DIRECTIONS:
        return _METRIC_DIRECTIONS[metric_name]
    if metric_name.startswith(WARNING_METRIC_PREFIX):
        return MetricDirection.LOWER_IS_BETTER
    raise KeyError(f"No direction registered for metric '{metric_name}'.")


@dataclass(frozen=True)
class Scorecard:
    """Frozen snapshot of the product-quality metrics for one screening run."""

    engine_version: str
    universe_size: int
    analyzed_count: int
    failed_count: int
    trade_count: int
    watchlist_count: int
    no_trade_count: int
    zero_confidence_count: int
    warning_counts: Mapping[str, int]

    def as_json(self) -> str:
        """Serialize metrics only. Excludes wall-clock time so re-runs of the
        same commit are byte-identical."""
        payload = {
            "engine_version": self.engine_version,
            "universe_size": self.universe_size,
            "analyzed_count": self.analyzed_count,
            "failed_count": self.failed_count,
            "trade_count": self.trade_count,
            "watchlist_count": self.watchlist_count,
            "no_trade_count": self.no_trade_count,
            "zero_confidence_count": self.zero_confidence_count,
            "warning_counts": dict(sorted(self.warning_counts.items())),
        }
        return json.dumps(payload, indent=2) + "\n"

    def as_markdown(self) -> str:
        """Render a Markdown table with every metric, phone-readable in a PR comment."""
        lines = ["| Metric | Value |", "| --- | --- |"]
        for name, value in self._scalar_metrics():
            lines.append(f"| {name} | {value} |")
        for warning_name, count in sorted(self.warning_counts.items()):
            lines.append(f"| warning: {warning_name} | {count} |")
        return "\n".join(lines) + "\n"

    def _scalar_metrics(self) -> Sequence[tuple[str, object]]:
        return (
            ("engine_version", self.engine_version),
            ("universe_size", self.universe_size),
            ("analyzed_count", self.analyzed_count),
            ("failed_count", self.failed_count),
            ("trade_count", self.trade_count),
            ("watchlist_count", self.watchlist_count),
            ("no_trade_count", self.no_trade_count),
            ("zero_confidence_count", self.zero_confidence_count),
        )


def build_scorecard(response: ScreeningResponse) -> Scorecard:
    """Compute a `Scorecard` from an existing `ScreeningResponse`.

    Pure function: no database access, no I/O.
    """
    zero_confidence_count = sum(
        1
        for item in response.items
        if item.status == "ok" and item.confidence is not None and item.confidence == 0.0
    )

    warning_counts: dict[str, int] = {}
    for item in response.items:
        for warning_name in item.warnings:
            warning_counts[warning_name] = warning_counts.get(warning_name, 0) + 1

    return Scorecard(
        engine_version=response.engine_version,
        universe_size=response.universe_size,
        analyzed_count=response.analyzed_count,
        failed_count=response.failed_count,
        trade_count=response.trade_count,
        watchlist_count=response.watchlist_count,
        no_trade_count=response.no_trade_count,
        zero_confidence_count=zero_confidence_count,
        warning_counts=dict(sorted(warning_counts.items())),
    )


@dataclass(frozen=True)
class MetricChange:
    """A single metric's value in the baseline versus the current run."""

    metric: str
    baseline: int
    current: int
    direction: MetricDirection

    def describe(self) -> str:
        return f"{self.metric}: baseline={self.baseline}, current={self.current}"


@dataclass(frozen=True)
class ScorecardDelta:
    """Tell, don't ask: callers read `improvements` / `regressions` /
    `has_regressions` instead of inspecting raw metric dictionaries."""

    improvements: Sequence[MetricChange] = field(default_factory=tuple)
    regressions: Sequence[MetricChange] = field(default_factory=tuple)
    unchanged: Sequence[MetricChange] = field(default_factory=tuple)

    @property
    def has_regressions(self) -> bool:
        return len(self.regressions) > 0

    def regression_report(self) -> str:
        """One line per regressed metric, naming the metric and both values."""
        return "\n".join(change.describe() for change in self.regressions)


def _metric_value(scorecard: Scorecard, metric: str) -> int:
    if metric.startswith(WARNING_METRIC_PREFIX):
        warning_name = metric[len(WARNING_METRIC_PREFIX):]
        return scorecard.warning_counts.get(warning_name, 0)
    return getattr(scorecard, metric)


def _all_metric_names(baseline: Scorecard, current: Scorecard) -> Sequence[str]:
    warning_names = sorted(set(baseline.warning_counts) | set(current.warning_counts))
    return tuple(_METRIC_DIRECTIONS.keys()) + tuple(
        f"{WARNING_METRIC_PREFIX}{warning_name}" for warning_name in warning_names
    )


def compare(baseline: Scorecard, current: Scorecard) -> ScorecardDelta:
    """Classify every metric as improved, regressed, or unchanged.

    NEUTRAL metrics are always reported as unchanged, regardless of their
    numeric delta, because they are allowed to move freely (see
    `MetricDirection` docstring).
    """
    improvements: list[MetricChange] = []
    regressions: list[MetricChange] = []
    unchanged: list[MetricChange] = []

    for metric in _all_metric_names(baseline, current):
        direction = metric_direction(metric)
        baseline_value = _metric_value(baseline, metric)
        current_value = _metric_value(current, metric)
        change = MetricChange(
            metric=metric,
            baseline=baseline_value,
            current=current_value,
            direction=direction,
        )

        if direction == MetricDirection.NEUTRAL or baseline_value == current_value:
            unchanged.append(change)
        elif direction == MetricDirection.LOWER_IS_BETTER:
            (regressions if current_value > baseline_value else improvements).append(change)
        else:  # HIGHER_IS_BETTER
            (regressions if current_value < baseline_value else improvements).append(change)

    return ScorecardDelta(
        improvements=tuple(improvements),
        regressions=tuple(regressions),
        unchanged=tuple(unchanged),
    )
