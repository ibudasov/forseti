#!/usr/bin/env python
"""TA-7 usefulness backtest for the Technical Analyst shadow signal.

Pure replay: this module consumes only persisted `technical_agent_effect`
rows (`app.db.models.AgentRun`) and stored `price_bar` rows. It makes no
model calls and no network calls of any kind, so it is safe to run
repeatedly and is fully covered by offline unit tests on synthetic bar
series (`tests/test_technical_backtest.py`).

Usefulness question: among shadow runs, does the "technical would
downgrade" cohort (`counterfactual_decision` ranked below `baseline_decision`)
show a lower forward 20-trading-day return than the "technical neutral"
cohort (`counterfactual_decision == baseline_decision`)? This script reports
the answer descriptively only -- it never claims statistical significance,
and its output must not be used to tune the Technical Analyst prompt or
policy that produced the same dataset without saying so explicitly in
`docs/trust/technical-shadow.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

from sqlmodel import select

from app.db.models import AgentRun, PriceBar, Security
from app.db.session import get_engine, get_session
from app.services.agent_run_dates import resolve_agent_run_as_of_date

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports" / "trust"
DEFAULT_HORIZON_TRADING_DAYS = 20
DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}


@dataclass(frozen=True)
class Bar:
    bar_date: date
    close: float


@dataclass(frozen=True)
class ShadowRunRecord:
    """One persisted shadow-mode `technical_agent_effect`, reduced to the
    fields the backtest needs.

    `as_of_date` must be the deterministic-pipeline as-of date for the run
    (not a model-call timestamp), so the forward-return anchor is stable
    and reproducible from stored bars alone.
    """

    run_id: str
    ticker: str
    as_of_date: date
    baseline_decision: str
    counterfactual_decision: str
    as_of_date_source: Optional[str] = None

    @property
    def is_downgrade(self) -> bool:
        return DECISION_RANK[self.counterfactual_decision] < DECISION_RANK[self.baseline_decision]


@dataclass(frozen=True)
class CohortSummary:
    label: str
    sample_size: int
    mean_forward_return: Optional[float]
    median_forward_return: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "sample_size": self.sample_size,
            "mean_forward_return": self.mean_forward_return,
            "median_forward_return": self.median_forward_return,
        }


@dataclass(frozen=True)
class BacktestReport:
    horizon_trading_days: int
    downgrade: CohortSummary
    neutral: CohortSummary
    excluded_insufficient_bars_count: int
    backfilled_rows_used: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon_trading_days": self.horizon_trading_days,
            "downgrade": self.downgrade.to_dict(),
            "neutral": self.neutral.to_dict(),
            "excluded_insufficient_bars_count": self.excluded_insufficient_bars_count,
            "backfilled_rows_used": self.backfilled_rows_used,
        }


def forward_return(bars: Sequence[Bar], as_of_date: date, horizon_trading_days: int) -> Optional[float]:
    """Return the simple forward return over `horizon_trading_days` trading
    days after the last bar on or before `as_of_date`.

    Returns `None` when there is no bar on or before `as_of_date`, or when
    there are fewer than `horizon_trading_days` bars after the anchor.
    `bars` must be sorted ascending by `bar_date` with no duplicate dates;
    this function reads only the values it is given.
    """
    anchor_index = _anchor_index(bars, as_of_date)
    if anchor_index is None:
        return None
    target_index = anchor_index + horizon_trading_days
    if target_index >= len(bars):
        return None
    anchor_close = bars[anchor_index].close
    if anchor_close == 0:
        return None
    future_close = bars[target_index].close
    return (future_close - anchor_close) / anchor_close


def _anchor_index(bars: Sequence[Bar], as_of_date: date) -> Optional[int]:
    anchor_index = None
    for index, bar in enumerate(bars):
        if bar.bar_date > as_of_date:
            break
        anchor_index = index
    return anchor_index


def build_backtest_report(
    records: Sequence[ShadowRunRecord],
    bars_by_ticker: dict[str, Sequence[Bar]],
    *,
    horizon_trading_days: int = DEFAULT_HORIZON_TRADING_DAYS,
) -> BacktestReport:
    downgrade_returns: list[float] = []
    neutral_returns: list[float] = []
    excluded_count = 0
    for record in records:
        bars = bars_by_ticker.get(record.ticker, ())
        outcome = forward_return(bars, record.as_of_date, horizon_trading_days)
        if outcome is None:
            excluded_count += 1
            continue
        cohort_returns = downgrade_returns if record.is_downgrade else neutral_returns
        cohort_returns.append(outcome)
    return BacktestReport(
        horizon_trading_days=horizon_trading_days,
        downgrade=_cohort_summary("technical_would_downgrade", downgrade_returns),
        neutral=_cohort_summary("technical_neutral", neutral_returns),
        excluded_insufficient_bars_count=excluded_count,
        backfilled_rows_used=sum(
            record.as_of_date_source == "created_at_backfill" for record in records
        ),
    )


def _cohort_summary(label: str, returns: list[float]) -> CohortSummary:
    if not returns:
        return CohortSummary(label=label, sample_size=0, mean_forward_return=None, median_forward_return=None)
    return CohortSummary(
        label=label,
        sample_size=len(returns),
        mean_forward_return=statistics.mean(returns),
        median_forward_return=statistics.median(returns),
    )


def render_markdown(report: BacktestReport) -> str:
    lines = [
        "# Technical shadow usefulness backtest",
        "",
        f"Forward-return horizon: {report.horizon_trading_days} trading days.",
        "",
        "| Cohort | Sample size | Mean forward return | Median forward return |",
        "| --- | --- | --- | --- |",
    ]
    for cohort in (report.downgrade, report.neutral):
        lines.append(
            f"| {cohort.label} | {cohort.sample_size} | "
            f"{_format_optional_float(cohort.mean_forward_return)} | "
            f"{_format_optional_float(cohort.median_forward_return)} |"
        )
    lines.extend(
        [
            "",
            f"excluded_insufficient_bars_count: {report.excluded_insufficient_bars_count}",
            f"backfilled_rows_used: {report.backfilled_rows_used}",
            "",
            "This report is descriptive only: sample sizes are reported alongside the means/medians, and "
            "no p-value or significance claim is made. Do not use these numbers to tune the Technical "
            "Analyst prompt or policy without saying so explicitly in docs/trust/technical-shadow.md.",
        ]
    )
    return "\n".join(lines) + "\n"


def _format_optional_float(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.4f}"


# --- DB-backed loading. Reads only locally-persisted rows; no model or network calls. ---


def load_shadow_records(engine: Any, *, limit: int = 500) -> list[ShadowRunRecord]:
    """Load persisted shadow-mode technical effects from `agent_runs`.

    Runs without an `as_of_date` fall back to `created_at.date()` with a
    warning. Backfilled rows are counted in the resulting report.
    """
    statement = (
        select(AgentRun)
        .where(AgentRun.technical_agent_effect.is_not(None))
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    with get_session(engine) as session:
        runs = session.exec(statement).all()

    records: list[ShadowRunRecord] = []
    for run in runs:
        effect = run.technical_agent_effect or {}
        if effect.get("mode") != "shadow":
            continue
        records.append(
            ShadowRunRecord(
                run_id=run.run_id,
                ticker=run.ticker,
                as_of_date=resolve_agent_run_as_of_date(run),
                baseline_decision=str(effect["baseline_decision"]),
                counterfactual_decision=str(effect["counterfactual_decision"]),
                as_of_date_source=run.as_of_date_source,
            )
        )
    return records


def load_bars_by_ticker(engine: Any, tickers: Sequence[str]) -> dict[str, list[Bar]]:
    bars_by_ticker: dict[str, list[Bar]] = {}
    for ticker in tickers:
        normalized_ticker = ticker.strip().upper()
        statement = (
            select(PriceBar)
            .join(Security)
            .where(Security.ticker == normalized_ticker)
            .order_by(PriceBar.bar_date.asc())
        )
        with get_session(engine) as session:
            rows = session.exec(statement).all()
        bars_by_ticker[normalized_ticker] = [Bar(bar_date=row.bar_date, close=float(row.close)) for row in rows]
    return bars_by_ticker


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500, help="Maximum persisted shadow runs to consider.")
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_TRADING_DAYS)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    engine = get_engine(os.environ.get("DATABASE_URL"))
    records = load_shadow_records(engine, limit=args.limit)
    bars_by_ticker = load_bars_by_ticker(engine, sorted({record.ticker for record in records}))
    report = build_backtest_report(records, bars_by_ticker, horizon_trading_days=args.horizon_days)

    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / "technical-shadow-backtest.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    from app.runtime import run_job

    sys.exit(run_job(main))
