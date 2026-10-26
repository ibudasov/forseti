#!/usr/bin/env python
"""CLI for the Technical Analyst golden evaluation suite.

Mirrors ``scripts/eval_critic.py``. Default mode replays the frozen, offline
fixtures in ``tests/fixtures/technical``. ``--live --confirm-cost yes`` also
runs the ``live_model`` fixtures through the real ``GeminiTechnicalModel``,
which requires ``VERTEX_AI_PROJECT``. ``--shadow-report`` aggregates
persisted ``technical_agent_effect`` rows instead of replaying fixtures.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from sqlmodel import select

from app.db.models import AgentRun, AgentRunStep
from app.db.session import get_engine, get_session
from app.services.agent_run_dates import render_agent_run_date_summary, summarize_agent_run_dates
from app.settings import get_settings
from tests.fixtures.technical.loader import (
    DEFAULT_FIXTURE_DIR,
    build_shadow_report,
    build_shadow_sample,
    evaluate_suite,
    load_technical_cases,
    render_shadow_markdown,
    render_suite_markdown,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run the live-mode confirmation gate, then replay live_model fixtures for real.",
    )
    parser.add_argument(
        "--confirm-cost",
        default="",
        help="Required when --live is used. Must be 'yes'.",
    )
    parser.add_argument(
        "--shadow-report",
        action="store_true",
        help="Aggregate persisted technical shadow-mode runs instead of fixture cases.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum persisted runs to include in --shadow-report mode.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Export up to N structured shadow-run rows in --shadow-report mode.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the shadow sample CSV to this path in --shadow-report mode.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _validate_shadow_report_export_args(args)
    if args.shadow_report:
        return _run_shadow_report(args)

    if args.live:
        _require_live_confirmation(args.confirm_cost)
    cases = load_technical_cases(args.fixture, include_live=args.live)
    result = evaluate_suite(cases, live=args.live)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(render_suite_markdown(result), end="")
    return 0 if result.passed else 1


def _run_shadow_report(args: argparse.Namespace) -> int:
    runs = _load_shadow_runs(limit=args.limit)
    steps = _load_shadow_steps([run.run_id for run in runs])
    report = build_shadow_report(runs, steps)
    shadow_runs = [
        run
        for run in runs
        if run.technical_agent_effect is not None
        and run.technical_agent_effect.get("mode") == "shadow"
    ]
    date_summary = summarize_agent_run_dates(shadow_runs)
    if args.sample > 0 and args.out is not None:
        sample = build_shadow_sample(runs, limit=args.sample)
        _write_shadow_sample(args.out, sample)
    if args.json:
        report_data = report.to_dict()
        report_data.update(date_summary)
        print(json.dumps(report_data, indent=2))
        return 0
    print(render_shadow_markdown(report) + render_agent_run_date_summary(date_summary), end="")
    return 0


def _require_live_confirmation(confirm_cost: str) -> None:
    if confirm_cost.lower() != "yes":
        raise RuntimeError("Live evaluation requires --confirm-cost yes.")
    if not get_settings().VERTEX_AI_PROJECT:
        raise RuntimeError("Live evaluation requires VERTEX_AI_PROJECT.")


def _load_shadow_runs(*, limit: int) -> list[AgentRun]:
    engine = get_engine(os.environ.get("DATABASE_URL"))
    statement = (
        select(AgentRun)
        .where(AgentRun.technical_agent_effect.is_not(None))
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    with get_session(engine) as session:
        return list(session.exec(statement).all())


def _load_shadow_steps(run_ids: list[str]) -> list[AgentRunStep]:
    if not run_ids:
        return []
    engine = get_engine(os.environ.get("DATABASE_URL"))
    statement = select(AgentRunStep).where(
        AgentRunStep.run_id.in_(run_ids),
        AgentRunStep.agent_name == "technical_analyst",
    )
    with get_session(engine) as session:
        return list(session.exec(statement).all())


def _validate_shadow_report_export_args(args: argparse.Namespace) -> None:
    if args.sample < 0:
        raise ValueError("--sample must be >= 0.")
    if args.out is not None and args.sample == 0:
        raise ValueError("--out requires --sample.")
    if args.sample > 0 and not args.shadow_report:
        raise ValueError("--sample requires --shadow-report.")
    if args.out is not None and not args.shadow_report:
        raise ValueError("--out requires --shadow-report.")


def _write_shadow_sample(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "ticker",
                "applied_adjustment",
                "final_decision",
                "decision_changed",
                "trend_state",
                "momentum_state",
                "reason_codes",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())


if __name__ == "__main__":
    from app.runtime import run_job

    sys.exit(run_job(main))
