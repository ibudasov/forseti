#!/usr/bin/env python
"""`make live-scorecard` entrypoint: compute the live product scorecard from the
configured application database (read-only).

Combines:
- Universe data coverage & screening `blocked_by` diagnostics (`build_universe_diagnostics`)
- Persisted shadow-mode reports for Fundamental, Technical, and Critic agents
- Offline forward-return usefulness backtest for Technical shadow runs
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from sqlmodel import col, select

from app.db.models import AgentRun, AgentRunStep
from app.db.session import get_engine, get_session
from app.services.fundamental_evaluation import build_shadow_report as build_fundamental_shadow
from app.services.live_scorecard import build_live_scorecard
from app.services.universe_diagnostics import build_universe_diagnostics
from scripts.technical_backtest import (
    DEFAULT_HORIZON_TRADING_DAYS,
    build_backtest_report,
    load_bars_by_ticker,
    load_shadow_records,
)


def _load_recent_runs(engine, *, limit: int) -> list[AgentRun]:
    with get_session(engine) as session:
        statement = select(AgentRun).order_by(col(AgentRun.created_at).desc()).limit(limit)
        return list(session.exec(statement).all())


def _load_steps(engine, run_ids: list[str]) -> list[AgentRunStep]:
    if not run_ids:
        return []
    with get_session(engine) as session:
        statement = select(AgentRunStep).where(col(AgentRunStep.run_id).in_(run_ids))
        return list(session.exec(statement).all())


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the live scorecard as JSON.")
    parser.add_argument("--markdown", action="store_true", help="Print the live scorecard as Markdown.")
    parser.add_argument("--limit", type=int, default=200, help="Max persisted agent runs to aggregate.")
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=DEFAULT_HORIZON_TRADING_DAYS,
        help="Forward-return horizon (trading days) for technical shadow backtest.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    from tests.fixtures.critic.loader import build_shadow_report as build_critic_shadow
    from tests.fixtures.technical.loader import build_shadow_report as build_technical_shadow

    args = parse_args(argv)
    engine = get_engine(os.environ.get("DATABASE_URL"))

    diagnostics = build_universe_diagnostics(engine=engine)
    runs = _load_recent_runs(engine, limit=args.limit)
    steps = _load_steps(engine, [run.run_id for run in runs])

    fundamental_shadow = build_fundamental_shadow(runs).model_dump(mode="json")
    technical_shadow = build_technical_shadow(runs, steps).to_dict()
    critic_shadow = build_critic_shadow(runs, steps).to_dict()

    shadow_records = load_shadow_records(engine=engine, limit=args.limit)
    bars_by_ticker = load_bars_by_ticker(
        engine,
        sorted({record.ticker for record in shadow_records}),
    )
    backtest_report = build_backtest_report(
        shadow_records,
        bars_by_ticker,
        horizon_trading_days=args.horizon_days,
    ).to_dict()

    scorecard = build_live_scorecard(
        diagnostics,
        fundamental_shadow=fundamental_shadow,
        technical_shadow=technical_shadow,
        critic_shadow=critic_shadow,
        technical_backtest=backtest_report,
    )

    if args.json:
        print(scorecard.as_json(), end="")
    if args.markdown or not args.json:
        print(scorecard.as_markdown(), end="")
    return 0


if __name__ == "__main__":
    from app.runtime import run_job

    sys.exit(run_job(main))
