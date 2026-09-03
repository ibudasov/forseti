"""Product scorecard: turn a `ScreeningResponse` into a comparable set of numbers.

`build_scorecard` is a pure function over the existing `ScreeningResponse` — no
DB access, no I/O, no wall-clock time. That is what makes it trivially
unit-testable to 100% and what makes two runs of the same commit produce
byte-identical JSON.

Metric direction (whether a bigger or smaller number is "better") lives in a
module-level lookup table rather than an if/else chain, per codestyle §15.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Tuple

from app.schemas.screening import ScreeningResponse


class MetricDirection(str, Enum):
    """Whether a metric moving up or down represents an improvement."""

    LOWER_IS_BETTER = "lower_is_better"
    HIGHER_IS_BETTER = "higher_is_better"
    NEUTRAL = "neutral"


# Diagnostic metrics must only ever improve. The trade/watchlist/no_trade split
# is deliberately NEUTRAL: ibudasov/forseti#25 will legitimately change that
# split once earnings data exists, and a gate that fires on those counts would
# block the very work it is meant to protect.
_CORE_METRIC_DIRECTIONS: Mapping[str, MetricDirection] = {
    "universe_size": MetricDirection.NEUTRAL,
    "analyzed_count": MetricDirection.NEUTRAL,
    "trade_count": MetricDirection.NEUTRAL,
    "watchlist_count": MetricDirection.NEUTRAL,
    "no_trade_count": MetricDirection.NEUTRAL,
    "failed_count": MetricDirection.LOWER_IS_BETTER,
    "zero_confidence_count": MetricDirection.LOWER_IS_BETTER,
}

# Every warning is a diagnostic: more items warning about missing or stale
# data is always worse, regardless of which warning it is.
_WARNING_DIRECTION = MetricDirection.LOWER_IS_BETTER

_CORE_METRIC_ORDER: Tuple[str, ...] = (
    "universe_size",
    "analyzed_count",
    "failed_count",
    "trade_count",
    "watchlist_count",
    "no_trade_count",
    "zero_confidence_count",
)


@dataclass(frozen=True)
class Scorecard:
    """A frozen snapshot of the metrics that describe product quality."""

    engine_version: str
    universe_size: int
    analyzed_count: int
    failed_count: int
    trade_count: int
    watchlist_count: int
    no_trade_count: int
    zero_confidence_count: int
    warning_counts: Mapping[str, int]

    def _core_metrics(self) -> Mapping[str, int]:
        return {name: getattr(self, name) for name in _CORE_METRIC_ORDER}

    def as_json(self) -> str:
        """Serialize to JSON, excluding wall-clock time so runs are comparable.

        `sort_keys=True` makes the output byte-identical across two runs of
        the same commit, regardless of dict insertion order.
        """
        payload = {
            "engine_version": self.engine_version,
            **self._core_metrics(),
            "warning_counts": dict(self.warning_counts),
        }
        return json.dumps(payload, sort_keys=True, indent=2) + "\n"

    def as_markdown(self) -> str:
        """Render as a Markdown table, safe to paste into a PR comment."""
        lines = [
            f"**Scorecard** — engine `{self.engine_version}`",
            "",
            "| Metric | Value |",
            "| --- | --- |",
        ]
        for name in _CORE_METRIC_ORDER:
            lines.append(f"| `{name}` | {getattr(self, name)} |")
        for key in sorted(self.warning_counts):
            lines.append(f"| warning: `{key}` | {self.warning_counts[key]} |")
        return "\n".join(lines) + "\n"


def build_scorecard(response: ScreeningResponse) -> Scorecard:
    """Compute a `Scorecard` from a `ScreeningResponse`. Pure function, no I/O."""
    zero_confidence_count = sum(
        1
        for item in response.items
        if item.status == "ok" and item.confidence is not None and item.confidence == 0.0
    )
    warning_counts = Counter(
        warning for item in response.items for warning in item.warnings
    )
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
    """The baseline-to-current change for a single metric."""

    name: str
    direction: MetricDirection
    baseline: int
    current: int

    @property
    def delta(self) -> int:
        return self.current - self.baseline

    def is_improvement(self) -> bool:
        if self.direction == MetricDirection.LOWER_IS_BETTER:
            return self.delta < 0
        if self.direction == MetricDirection.HIGHER_IS_BETTER:
            return self.delta > 0
        return False

    def is_regression(self) -> bool:
        if self.direction == MetricDirection.LOWER_IS_BETTER:
            return self.delta > 0
        if self.direction == MetricDirection.HIGHER_IS_BETTER:
            return self.delta < 0
        return False

    def failure_message(self) -> str:
        return (
            f"{self.name} regressed: baseline={self.baseline} current={self.current} "
            f"(delta={self.delta:+d})"
        )


@dataclass(frozen=True)
class ScorecardDelta:
    """Tell, don't ask: callers read `improvements`/`regressions`/`has_regressions`,
    never raw dicts, to decide whether an iteration made things better or worse.
    """

    changes: Tuple[MetricChange, ...]

    @property
    def improvements(self) -> Tuple[MetricChange, ...]:
        return tuple(change for change in self.changes if change.is_improvement())

    @property
    def regressions(self) -> Tuple[MetricChange, ...]:
        return tuple(change for change in self.changes if change.is_regression())

    @property
    def has_regressions(self) -> bool:
        return len(self.regressions) > 0

    def as_markdown(self) -> str:
        """Render baseline vs. current vs. delta, safe to paste into a PR comment."""
        lines = [
            "| Metric | Baseline | Current | Delta |",
            "| --- | --- | --- | --- |",
        ]
        for change in self.changes:
            marker = ""
            if change.is_improvement():
                marker = " ✅"
            elif change.is_regression():
                marker = " ⚠️"
            lines.append(
                f"| `{change.name}` | {change.baseline} | {change.current} | "
                f"{change.delta:+d}{marker} |"
            )
        return "\n".join(lines) + "\n"


def _metric_direction(name: str) -> MetricDirection:
    return _CORE_METRIC_DIRECTIONS.get(name, _WARNING_DIRECTION)


def compare(baseline: Scorecard, current: Scorecard) -> ScorecardDelta:
    """Compare two scorecards metric by metric, including the warning histogram."""
    changes = []
    for name in _CORE_METRIC_ORDER:
        changes.append(
            MetricChange(
                name=name,
                direction=_metric_direction(name),
                baseline=getattr(baseline, name),
                current=getattr(current, name),
            )
        )

    warning_keys = sorted(set(baseline.warning_counts) | set(current.warning_counts))
    for key in warning_keys:
        changes.append(
            MetricChange(
                name=f"warning:{key}",
                direction=_metric_direction(key),
                baseline=baseline.warning_counts.get(key, 0),
                current=current.warning_counts.get(key, 0),
            )
        )

    return ScorecardDelta(changes=tuple(changes))
