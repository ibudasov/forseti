"""Live / Ops product scorecard: aggregate real database coverage, screening
diagnostics, persisted shadow-mode agent reports, and forward-return backtests.

Pure functions over already-loaded report objects/dicts — no DB access, no I/O,
no network calls. `scripts/live_scorecard.py` handles all database I/O.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

from app.schemas.diagnostics import UniverseDiagnosticsResponse

SHADOW_PROMOTION_TARGET_RUNS = 100


def _coverage_pct(count: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(count / total) * 100.0:.1f}%"


def _format_optional_float(value: Optional[float], *, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


def _format_return(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:+.2f}%"


def _as_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _flatten_blocked_by(blocked_by: Mapping[str, Any]) -> dict[str, int]:
    flat: dict[str, int] = {}
    for stage, value in sorted(blocked_by.items()):
        if isinstance(value, Mapping):
            for rule_id, count in sorted(value.items()):
                flat[f"{stage}/{rule_id}"] = int(count)
        elif isinstance(value, int) and value > 0:
            flat[stage] = value
    return flat


@dataclass(frozen=True)
class UniverseCoverageSnapshot:
    universe_size: int
    tickers_with_price_bars: int
    tickers_with_200_bars: int
    tickers_with_technical_features: int
    tickers_with_fundamentals: int
    tickers_with_earnings_events: int
    tickers_with_document_chunks: int
    latest_macro_daily_date: Optional[str]
    blocked_by: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_size": self.universe_size,
            "tickers_with_price_bars": self.tickers_with_price_bars,
            "tickers_with_200_bars": self.tickers_with_200_bars,
            "tickers_with_technical_features": self.tickers_with_technical_features,
            "tickers_with_fundamentals": self.tickers_with_fundamentals,
            "tickers_with_earnings_events": self.tickers_with_earnings_events,
            "tickers_with_document_chunks": self.tickers_with_document_chunks,
            "latest_macro_daily_date": self.latest_macro_daily_date,
            "blocked_by": dict(self.blocked_by),
        }


@dataclass(frozen=True)
class ShadowAgentSummary:
    agent: str
    run_count: int
    target_runs: int = SHADOW_PROMOTION_TARGET_RUNS
    decision_change_rate: Optional[float] = None
    p50_latency_ms: Optional[float] = None
    p90_latency_ms: Optional[float] = None
    estimated_cost_usd: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "run_count": self.run_count,
            "target_runs": self.target_runs,
            "decision_change_rate": self.decision_change_rate,
            "p50_latency_ms": self.p50_latency_ms,
            "p90_latency_ms": self.p90_latency_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass(frozen=True)
class BacktestSnapshot:
    horizon_trading_days: int
    downgrade_sample_size: int
    downgrade_mean_return: Optional[float]
    neutral_sample_size: int
    neutral_mean_return: Optional[float]
    excluded_insufficient_bars_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon_trading_days": self.horizon_trading_days,
            "downgrade_sample_size": self.downgrade_sample_size,
            "downgrade_mean_return": self.downgrade_mean_return,
            "neutral_sample_size": self.neutral_sample_size,
            "neutral_mean_return": self.neutral_mean_return,
            "excluded_insufficient_bars_count": self.excluded_insufficient_bars_count,
        }


@dataclass(frozen=True)
class LiveScorecard:
    engine_version: str
    universe: UniverseCoverageSnapshot
    shadow_agents: Tuple[ShadowAgentSummary, ...] = ()
    backtest: Optional[BacktestSnapshot] = None

    def as_json(self) -> str:
        payload = {
            "engine_version": self.engine_version,
            "universe": self.universe.to_dict(),
            "shadow_agents": [agent.to_dict() for agent in self.shadow_agents],
            "backtest": self.backtest.to_dict() if self.backtest is not None else None,
        }
        return json.dumps(payload, sort_keys=True, indent=2) + "\n"

    def as_markdown(self) -> str:
        lines = [f"## Live Product Scorecard — engine `{self.engine_version}`"]
        lines.extend(self._universe_markdown())
        if self.universe.blocked_by:
            lines.extend(self._blocked_by_markdown())
        if self.shadow_agents:
            lines.extend(self._shadow_markdown())
        if self.backtest is not None:
            lines.extend(self._backtest_markdown())
        return "\n".join(lines) + "\n"

    def _universe_markdown(self) -> list[str]:
        u = self.universe
        total = u.universe_size
        rows = (
            ("Active universe", f"{total}", "100.0%" if total > 0 else "0.0%"),
            ("Price bars (>0)", f"{u.tickers_with_price_bars}/{total}", _coverage_pct(u.tickers_with_price_bars, total)),
            ("Price bars (>=200)", f"{u.tickers_with_200_bars}/{total}", _coverage_pct(u.tickers_with_200_bars, total)),
            (
                "Technical features",
                f"{u.tickers_with_technical_features}/{total}",
                _coverage_pct(u.tickers_with_technical_features, total),
            ),
            ("Fundamentals", f"{u.tickers_with_fundamentals}/{total}", _coverage_pct(u.tickers_with_fundamentals, total)),
            (
                "Earnings events",
                f"{u.tickers_with_earnings_events}/{total}",
                _coverage_pct(u.tickers_with_earnings_events, total),
            ),
            (
                "RAG document chunks",
                f"{u.tickers_with_document_chunks}/{total}",
                _coverage_pct(u.tickers_with_document_chunks, total),
            ),
            ("Latest macro date", u.latest_macro_daily_date or "none", "-"),
        )
        lines = [
            "",
            "### Universe Data Coverage",
            "",
            "| Dimension | Count | Coverage |",
            "| --- | ---: | ---: |",
        ]
        for label, count_str, pct_str in rows:
            lines.append(f"| {label} | {count_str} | {pct_str} |")
        return lines

    def _blocked_by_markdown(self) -> list[str]:
        lines = [
            "",
            "### Screening Outcomes (`blocked_by`)",
            "",
            "| Stage / Rule | Tickers |",
            "| --- | ---: |",
        ]
        for bucket, count in sorted(self.universe.blocked_by.items()):
            lines.append(f"| `{bucket}` | {count} |")
        return lines

    def _shadow_markdown(self) -> list[str]:
        lines = [
            "",
            "### Shadow-Mode Agent Progress",
            "",
            "| Agent | Runs (vs target) | Decision Change Rate | p50 Latency | Est. Cost (USD) |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for agent in self.shadow_agents:
            change_rate = (
                f"{agent.decision_change_rate * 100.0:.1f}%"
                if agent.decision_change_rate is not None
                else "n/a"
            )
            latency = _format_optional_float(agent.p50_latency_ms, suffix=" ms", digits=1)
            cost = _format_optional_float(agent.estimated_cost_usd, suffix="", digits=4)
            lines.append(
                f"| `{agent.agent}` | {agent.run_count}/{agent.target_runs} | "
                f"{change_rate} | {latency} | {cost} |"
            )
        return lines

    def _backtest_markdown(self) -> list[str]:
        assert self.backtest is not None
        bt = self.backtest
        return [
            "",
            f"### Technical Shadow Backtest ({bt.horizon_trading_days}-day forward return)",
            "",
            "| Cohort | Sample Size | Mean Forward Return |",
            "| --- | ---: | ---: |",
            f"| `technical_would_downgrade` | {bt.downgrade_sample_size} | {_format_return(bt.downgrade_mean_return)} |",
            f"| `technical_neutral` | {bt.neutral_sample_size} | {_format_return(bt.neutral_mean_return)} |",
            f"| Excluded (insufficient bars) | {bt.excluded_insufficient_bars_count} | - |",
        ]


def _fundamental_shadow_summary(report: Mapping[str, Any]) -> ShadowAgentSummary:
    run_count = int(report.get("run_count", 0))
    outcomes = report.get("outcome_counts") or {}
    changed = int(outcomes.get("downgrade", 0)) + int(outcomes.get("upgrade", 0))
    change_rate = (changed / run_count) if run_count > 0 else None
    latency = report.get("latency_ms") or {}
    tokens = report.get("token_usage") or {}
    return ShadowAgentSummary(
        agent="fundamental",
        run_count=run_count,
        decision_change_rate=change_rate,
        p50_latency_ms=_as_optional_float(latency.get("p50")),
        p90_latency_ms=_as_optional_float(latency.get("p90")),
        estimated_cost_usd=_as_optional_float(tokens.get("estimated_cost_usd")),
    )


def _technical_shadow_summary(report: Mapping[str, Any]) -> ShadowAgentSummary:
    run_count = int(report.get("run_count", 0))
    latency = report.get("latency_ms") or {}
    tokens = report.get("token_usage") or {}
    return ShadowAgentSummary(
        agent="technical",
        run_count=run_count,
        decision_change_rate=_as_optional_float(report.get("decision_change_rate")) if run_count > 0 else None,
        p50_latency_ms=_as_optional_float(latency.get("p50")),
        p90_latency_ms=_as_optional_float(latency.get("p90")),
        estimated_cost_usd=_as_optional_float(tokens.get("estimated_cost_usd")),
    )


def _critic_shadow_summary(report: Mapping[str, Any]) -> ShadowAgentSummary:
    run_count = int(report.get("run_count", 0))
    return ShadowAgentSummary(
        agent="critic",
        run_count=run_count,
        decision_change_rate=_as_optional_float(report.get("decision_change_rate")) if run_count > 0 else None,
    )


def _backtest_snapshot(report: Mapping[str, Any]) -> BacktestSnapshot:
    downgrade = report.get("downgrade") or {}
    neutral = report.get("neutral") or {}
    return BacktestSnapshot(
        horizon_trading_days=int(report.get("horizon_trading_days", 20)),
        downgrade_sample_size=int(downgrade.get("sample_size", 0)),
        downgrade_mean_return=_as_optional_float(downgrade.get("mean_forward_return")),
        neutral_sample_size=int(neutral.get("sample_size", 0)),
        neutral_mean_return=_as_optional_float(neutral.get("mean_forward_return")),
        excluded_insufficient_bars_count=int(report.get("excluded_insufficient_bars_count", 0)),
    )


def build_live_scorecard(
    diagnostics: UniverseDiagnosticsResponse,
    *,
    fundamental_shadow: Optional[Mapping[str, Any]] = None,
    technical_shadow: Optional[Mapping[str, Any]] = None,
    critic_shadow: Optional[Mapping[str, Any]] = None,
    technical_backtest: Optional[Mapping[str, Any]] = None,
) -> LiveScorecard:
    """Build a `LiveScorecard` from real database diagnostics and shadow reports."""
    cov = diagnostics.coverage
    macro_date = cov.latest_macro_daily_date.isoformat() if cov.latest_macro_daily_date else None
    universe = UniverseCoverageSnapshot(
        universe_size=diagnostics.universe_size,
        tickers_with_price_bars=cov.tickers_with_price_bars,
        tickers_with_200_bars=cov.tickers_with_200_bars,
        tickers_with_technical_features=cov.tickers_with_technical_features,
        tickers_with_fundamentals=cov.tickers_with_fundamentals,
        tickers_with_earnings_events=cov.tickers_with_earnings_events,
        tickers_with_document_chunks=cov.tickers_with_document_chunks,
        latest_macro_daily_date=macro_date,
        blocked_by=_flatten_blocked_by(diagnostics.blocked_by),
    )

    shadow_agents: list[ShadowAgentSummary] = []
    if fundamental_shadow is not None:
        shadow_agents.append(_fundamental_shadow_summary(fundamental_shadow))
    if technical_shadow is not None:
        shadow_agents.append(_technical_shadow_summary(technical_shadow))
    if critic_shadow is not None:
        shadow_agents.append(_critic_shadow_summary(critic_shadow))

    backtest = _backtest_snapshot(technical_backtest) if technical_backtest is not None else None

    return LiveScorecard(
        engine_version=diagnostics.engine_version,
        universe=universe,
        shadow_agents=tuple(shadow_agents),
        backtest=backtest,
    )
