#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlmodel import select

from app.db.models import AgentRun
from app.db.session import get_engine, get_session
from app.services.fundamental_evaluation import (
    DEFAULT_EVALUATION_FIXTURE,
    build_shadow_report,
    evaluate_live_suite,
    evaluate_recorded_suite,
    has_unpriced_cost,
    load_fundamental_evaluation_suite,
    render_evaluation_markdown,
    render_shadow_markdown,
)
from app.services.agent_run_dates import render_agent_run_date_summary, summarize_agent_run_dates
from app.settings import get_settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_EVALUATION_FIXTURE)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--live", action="store_true", help="Run live model calls against the frozen request snapshots.")
    parser.add_argument(
        "--confirm-cost",
        default="",
        help="Required when --live is used. Must be 'yes'.",
    )
    parser.add_argument(
        "--shadow-report",
        action="store_true",
        help="Aggregate persisted shadow-mode runs instead of fixture cases.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum persisted runs to include in --shadow-report mode.",
    )
    parser.add_argument(
        "--allow-unpriced",
        action="store_true",
        help="Do not fail when the report cannot price its token usage.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.shadow_report:
        runs = _load_shadow_runs(limit=args.limit)
        shadow_runs = [
            run
            for run in runs
            if run.fundamental_agent_effect is not None
            and run.fundamental_agent_effect.get("mode") == "shadow"
        ]
        report = build_shadow_report(runs)
        date_summary = summarize_agent_run_dates(shadow_runs)
        if args.json:
            report_data = report.model_dump(mode="json")
            report_data.update(date_summary)
            print(json.dumps(report_data, indent=2))
            return _unpriced_exit_code(report.token_usage, args.allow_unpriced)
        print(render_shadow_markdown(report) + render_agent_run_date_summary(date_summary), end="")
        return _unpriced_exit_code(report.token_usage, args.allow_unpriced)

    suite = load_fundamental_evaluation_suite(args.fixture)
    if args.live:
        _require_live_confirmation(args.confirm_cost)
        report = evaluate_live_suite(suite)
    else:
        report = evaluate_recorded_suite(suite)

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print(render_evaluation_markdown(report), end="")
    if _unpriced_exit_code(report.metrics.token_usage, args.allow_unpriced):
        return 1
    return 0 if report.gates.passed else 1


def _unpriced_exit_code(token_usage: dict[str, float | None], allow_unpriced: bool) -> int:
    if allow_unpriced or not has_unpriced_cost(token_usage):
        return 0
    print("Error: report contains unpriced runs; update app/pricing/catalog.json or pass --allow-unpriced.", file=sys.stderr)
    return 1


def _require_live_confirmation(confirm_cost: str) -> None:
    if confirm_cost.lower() != "yes":
        raise RuntimeError("Live evaluation requires --confirm-cost yes.")
    if not get_settings().VERTEX_AI_PROJECT:
        raise RuntimeError("Live evaluation requires VERTEX_AI_PROJECT.")


def _load_shadow_runs(*, limit: int) -> list[AgentRun]:
    engine = get_engine(os.environ.get("DATABASE_URL"))
    statement = (
        select(AgentRun)
        .where(AgentRun.fundamental_agent_effect.is_not(None))
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    with get_session(engine) as session:
        return list(session.exec(statement).all())


if __name__ == "__main__":
    from app.runtime import run_job

    sys.exit(run_job(main))
