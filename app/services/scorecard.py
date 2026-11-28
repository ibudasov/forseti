"""Product scorecard: turn screening and offline agent evaluations into comparable numbers.

`build_scorecard` is a pure function over a `ScreeningResponse` plus optional
offline agent evaluation summaries — no DB access, no I/O, no wall-clock time.
Two runs of the same commit produce byte-identical JSON.

Metric direction (whether a bigger or smaller number is "better") lives in
module-level lookup tables rather than an if/else chain, per codestyle §15.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Tuple

from app.schemas.screening import ScreeningItem, ScreeningResponse

MetricNumber = int | float
_FLOAT_TOLERANCE = 1e-9


class MetricDirection(str, Enum):
    """Whether a metric moving up or down represents an improvement."""

    LOWER_IS_BETTER = "lower_is_better"
    HIGHER_IS_BETTER = "higher_is_better"
    NEUTRAL = "neutral"


# Diagnostic metrics must only ever improve. The trade/watchlist/no_trade split
# is deliberately NEUTRAL: legitimate rule or data changes reshuffle that split.
_CORE_METRIC_DIRECTIONS: Mapping[str, MetricDirection] = {
    "universe_size": MetricDirection.NEUTRAL,
    "analyzed_count": MetricDirection.NEUTRAL,
    "trade_count": MetricDirection.NEUTRAL,
    "watchlist_count": MetricDirection.NEUTRAL,
    "no_trade_count": MetricDirection.NEUTRAL,
    "failed_count": MetricDirection.LOWER_IS_BETTER,
    "zero_confidence_count": MetricDirection.LOWER_IS_BETTER,
}

_AGENT_EVAL_DIRECTIONS: Mapping[str, MetricDirection] = {
    "synthesizer.total_cases": MetricDirection.NEUTRAL,
    "synthesizer.valid_schema_rate": MetricDirection.HIGHER_IS_BETTER,
    "synthesizer.citation_precision": MetricDirection.HIGHER_IS_BETTER,
    "synthesizer.numeric_claim_accuracy": MetricDirection.HIGHER_IS_BETTER,
    "synthesizer.downgrade_only_compliance": MetricDirection.HIGHER_IS_BETTER,
    "synthesizer.fallback_rate": MetricDirection.LOWER_IS_BETTER,
    "synthesizer.promotion_count": MetricDirection.LOWER_IS_BETTER,
    "synthesizer.money_field_count": MetricDirection.LOWER_IS_BETTER,
    "synthesizer.rejected_without_fallback_count": MetricDirection.LOWER_IS_BETTER,
    "fundamental.case_count": MetricDirection.NEUTRAL,
    "fundamental.structured_output_validity_rate": MetricDirection.HIGHER_IS_BETTER,
    "fundamental.citation_validity_rate": MetricDirection.HIGHER_IS_BETTER,
    "fundamental.material_claim_citation_coverage": MetricDirection.HIGHER_IS_BETTER,
    "fundamental.unsupported_claim_rate": MetricDirection.LOWER_IS_BETTER,
    "fundamental.agreement_rate": MetricDirection.HIGHER_IS_BETTER,
    "fundamental.hard_blocker_violation_count": MetricDirection.LOWER_IS_BETTER,
    "fundamental.risk_field_mutation_count": MetricDirection.LOWER_IS_BETTER,
    "fundamental.neutral_fallback_rate": MetricDirection.LOWER_IS_BETTER,
    "technical.total_cases": MetricDirection.NEUTRAL,
    "technical.passed_cases": MetricDirection.HIGHER_IS_BETTER,
    "technical.failed_cases": MetricDirection.LOWER_IS_BETTER,
    "technical.pass_rate": MetricDirection.HIGHER_IS_BETTER,
    "critic.total_cases": MetricDirection.NEUTRAL,
    "critic.passed_cases": MetricDirection.HIGHER_IS_BETTER,
    "critic.failed_cases": MetricDirection.LOWER_IS_BETTER,
    "critic.pass_rate": MetricDirection.HIGHER_IS_BETTER,
}

# Every warning is a diagnostic: more items warning about missing or stale
# data is always worse, regardless of which warning it is.
_WARNING_DIRECTION = MetricDirection.LOWER_IS_BETTER
_BLOCKED_BY_DIRECTION = MetricDirection.NEUTRAL

_CORE_METRIC_ORDER: Tuple[str, ...] = (
    "universe_size",
    "analyzed_count",
    "failed_count",
    "trade_count",
    "watchlist_count",
    "no_trade_count",
    "zero_confidence_count",
)

_AGENT_SUITE_ORDER: Tuple[str, ...] = (
    "synthesizer",
    "fundamental",
    "technical",
    "critic",
)


def _format_value(value: MetricNumber) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _format_delta(value: MetricNumber) -> str:
    if isinstance(value, float):
        return f"{value:+.3f}"
    return f"{value:+d}"


def _round_rate(value: float) -> float:
    return round(float(value), 6)


def extract_synthesizer_metrics(report: Mapping[str, Any]) -> dict[str, MetricNumber]:
    """Extract comparable product-quality metrics from a synthesizer eval report."""
    metrics = report.get("metrics") or {}
    return {
        "total_cases": int(report.get("total_cases", 0)),
        "valid_schema_rate": _round_rate(metrics.get("valid_schema_rate", 0.0)),
        "citation_precision": _round_rate(metrics.get("citation_precision", 0.0)),
        "numeric_claim_accuracy": _round_rate(metrics.get("numeric_claim_accuracy", 0.0)),
        "downgrade_only_compliance": _round_rate(metrics.get("downgrade_only_compliance", 0.0)),
        "fallback_rate": _round_rate(metrics.get("fallback_rate", 0.0)),
        "promotion_count": int(report.get("promotion_count", 0)),
        "money_field_count": int(report.get("money_field_count", 0)),
        "rejected_without_fallback_count": int(report.get("rejected_without_fallback_count", 0)),
    }


def extract_fundamental_metrics(report: Mapping[str, Any]) -> dict[str, MetricNumber]:
    """Extract comparable product-quality metrics from a fundamental-agent eval report."""
    metrics = report.get("metrics") or {}
    return {
        "case_count": int(metrics.get("case_count", 0)),
        "structured_output_validity_rate": _round_rate(metrics.get("structured_output_validity_rate", 0.0)),
        "citation_validity_rate": _round_rate(metrics.get("citation_validity_rate", 0.0)),
        "material_claim_citation_coverage": _round_rate(metrics.get("material_claim_citation_coverage", 0.0)),
        "unsupported_claim_rate": _round_rate(metrics.get("unsupported_claim_rate", 0.0)),
        "agreement_rate": _round_rate(metrics.get("agreement_rate", 0.0)),
        "hard_blocker_violation_count": int(metrics.get("hard_blocker_violation_count", 0)),
        "risk_field_mutation_count": int(metrics.get("risk_field_mutation_count", 0)),
        "neutral_fallback_rate": _round_rate(metrics.get("neutral_fallback_rate", 0.0)),
    }


def extract_suite_metrics(report: Mapping[str, Any]) -> dict[str, MetricNumber]:
    """Extract pass/fail metrics from a golden suite report (critic or technical)."""
    total_cases = int(report.get("total_cases", 0))
    passed_cases = int(report.get("passed_cases", 0))
    failed_cases = int(report.get("failed_cases", 0))
    pass_rate = _round_rate(passed_cases / total_cases) if total_cases > 0 else 0.0
    return {
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "pass_rate": pass_rate,
    }


def _blocked_by_bucket(item: ScreeningItem) -> str:
    if item.status == "error":
        return "error"
    if item.decision == "trade":
        return "passed"
    if item.debug_reason:
        return item.debug_reason.split(":", 1)[0].strip()
    return "unspecified"


def _blocked_by_counts(items: list[ScreeningItem]) -> dict[str, int]:
    if not any(item.debug_reason for item in items):
        return {}
    counts = Counter(_blocked_by_bucket(item) for item in items)
    return dict(sorted(counts.items()))


def _ordered_suites(agent_evals: Mapping[str, Mapping[str, MetricNumber]]) -> list[str]:
    ordered = [suite for suite in _AGENT_SUITE_ORDER if suite in agent_evals]
    extras = sorted(suite for suite in agent_evals if suite not in _AGENT_SUITE_ORDER)
    return ordered + extras


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
    blocked_by_counts: Mapping[str, int] = field(default_factory=dict)
    agent_evals: Mapping[str, Mapping[str, MetricNumber]] = field(default_factory=dict)

    def _core_metrics(self) -> Mapping[str, int]:
        return {name: getattr(self, name) for name in _CORE_METRIC_ORDER}

    def as_json(self) -> str:
        """Serialize to JSON, excluding wall-clock time so runs are comparable.

        `sort_keys=True` makes the output byte-identical across two runs of
        the same commit, regardless of dict insertion order.
        """
        payload: dict[str, Any] = {
            "engine_version": self.engine_version,
            **self._core_metrics(),
            "warning_counts": dict(self.warning_counts),
            "blocked_by_counts": dict(self.blocked_by_counts),
            "agent_evals": {
                suite: dict(sorted(metrics.items()))
                for suite, metrics in sorted(self.agent_evals.items())
            },
        }
        return json.dumps(payload, sort_keys=True, indent=2) + "\n"

    def as_markdown(self) -> str:
        """Render as Markdown tables, safe to paste into a PR comment."""
        lines = [f"**Product Scorecard** — engine `{self.engine_version}`"]
        if self.agent_evals:
            lines.extend(self._agent_eval_markdown())
        lines.extend(self._deterministic_markdown())
        return "\n".join(lines) + "\n"

    def _agent_eval_markdown(self) -> list[str]:
        lines = [
            "",
            "### Agent & Synthesizer Offline Evals",
            "",
            "| Suite | Metric | Value |",
            "| --- | --- | ---: |",
        ]
        for suite in _ordered_suites(self.agent_evals):
            metrics = self.agent_evals[suite]
            for name in sorted(metrics):
                lines.append(f"| `{suite}` | `{name}` | {_format_value(metrics[name])} |")
        return lines

    def _deterministic_markdown(self) -> list[str]:
        lines = [
            "",
            "### Deterministic Engine Regression (Frozen Fixture)",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
        ]
        for name in _CORE_METRIC_ORDER:
            lines.append(f"| `{name}` | {getattr(self, name)} |")
        for key in sorted(self.blocked_by_counts):
            lines.append(f"| blocked_by: `{key}` | {self.blocked_by_counts[key]} |")
        for key in sorted(self.warning_counts):
            lines.append(f"| warning: `{key}` | {self.warning_counts[key]} |")
        return lines


def build_scorecard(
    response: ScreeningResponse,
    *,
    agent_evals: Mapping[str, Mapping[str, MetricNumber]] | None = None,
) -> Scorecard:
    """Compute a `Scorecard` from a `ScreeningResponse` and optional agent evals."""
    zero_confidence_count = sum(
        1
        for item in response.items
        if item.status == "ok" and item.confidence is not None and item.confidence == 0.0
    )
    warning_counts = Counter(
        warning for item in response.items for warning in item.warnings
    )
    normalized_evals = {
        suite: dict(sorted(metrics.items()))
        for suite, metrics in sorted((agent_evals or {}).items())
    }
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
        blocked_by_counts=_blocked_by_counts(response.items),
        agent_evals=normalized_evals,
    )


@dataclass(frozen=True)
class MetricChange:
    """The baseline-to-current change for a single metric."""

    name: str
    direction: MetricDirection
    baseline: MetricNumber
    current: MetricNumber

    @property
    def delta(self) -> MetricNumber:
        if isinstance(self.baseline, int) and isinstance(self.current, int):
            return self.current - self.baseline
        return round(float(self.current) - float(self.baseline), 6)

    def is_improvement(self) -> bool:
        diff = float(self.current) - float(self.baseline)
        if self.direction == MetricDirection.LOWER_IS_BETTER:
            return diff < -_FLOAT_TOLERANCE
        if self.direction == MetricDirection.HIGHER_IS_BETTER:
            return diff > _FLOAT_TOLERANCE
        return False

    def is_regression(self) -> bool:
        diff = float(self.current) - float(self.baseline)
        if self.direction == MetricDirection.LOWER_IS_BETTER:
            return diff > _FLOAT_TOLERANCE
        if self.direction == MetricDirection.HIGHER_IS_BETTER:
            return diff < -_FLOAT_TOLERANCE
        return False

    def failure_message(self) -> str:
        return (
            f"{self.name} regressed: baseline={_format_value(self.baseline)} "
            f"current={_format_value(self.current)} (delta={_format_delta(self.delta)})"
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
            "",
            "### Baseline Comparison",
            "",
            "| Metric | Baseline | Current | Delta |",
            "| --- | ---: | ---: | ---: |",
        ]
        for change in self.changes:
            marker = ""
            if change.is_improvement():
                marker = " ✅"
            elif change.is_regression():
                marker = " ⚠️"
            lines.append(
                f"| `{change.name}` | {_format_value(change.baseline)} | "
                f"{_format_value(change.current)} | {_format_delta(change.delta)}{marker} |"
            )
        return "\n".join(lines) + "\n"


def _metric_direction(name: str) -> MetricDirection:
    if name in _CORE_METRIC_DIRECTIONS:
        return _CORE_METRIC_DIRECTIONS[name]
    if name in _AGENT_EVAL_DIRECTIONS:
        return _AGENT_EVAL_DIRECTIONS[name]
    if name.startswith("blocked_by:"):
        return _BLOCKED_BY_DIRECTION
    return _WARNING_DIRECTION


def _compare_agent_evals(baseline: Scorecard, current: Scorecard) -> list[MetricChange]:
    changes: list[MetricChange] = []
    suites = sorted(set(baseline.agent_evals) | set(current.agent_evals))
    ordered = [suite for suite in _AGENT_SUITE_ORDER if suite in suites]
    ordered.extend(suite for suite in suites if suite not in _AGENT_SUITE_ORDER)

    for suite in ordered:
        base_suite = baseline.agent_evals.get(suite, {})
        curr_suite = current.agent_evals.get(suite, {})
        for key in sorted(set(base_suite) | set(curr_suite)):
            metric_name = f"{suite}.{key}"
            changes.append(
                MetricChange(
                    name=metric_name,
                    direction=_metric_direction(metric_name),
                    baseline=base_suite.get(key, 0),
                    current=curr_suite.get(key, 0),
                )
            )
    return changes


def compare(baseline: Scorecard, current: Scorecard) -> ScorecardDelta:
    """Compare two scorecards metric by metric, including agent evals and diagnostics."""
    changes: list[MetricChange] = []
    changes.extend(_compare_agent_evals(baseline, current))

    for name in _CORE_METRIC_ORDER:
        changes.append(
            MetricChange(
                name=name,
                direction=_metric_direction(name),
                baseline=getattr(baseline, name),
                current=getattr(current, name),
            )
        )

    blocked_keys = sorted(set(baseline.blocked_by_counts) | set(current.blocked_by_counts))
    for key in blocked_keys:
        metric_name = f"blocked_by:{key}"
        changes.append(
            MetricChange(
                name=metric_name,
                direction=_metric_direction(metric_name),
                baseline=baseline.blocked_by_counts.get(key, 0),
                current=current.blocked_by_counts.get(key, 0),
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
