#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlmodel import select

from app.db.models import AgentRun, AgentRunStep
from app.db.session import get_engine, get_session
from app.settings import get_settings
from tests.fixtures.critic.loader import (
    DEFAULT_FIXTURE_DIR,
    CriticSuiteResult,
    build_shadow_report,
    critic_case_paths,
    evaluate_suite,
    load_critic_case,
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
        help="Run the live-mode confirmation gate before replaying the frozen suite.",
    )
    parser.add_argument(
        "--confirm-cost",
        default="",
        help="Required when --live is used. Must be 'yes'.",
    )
    parser.add_argument(
        "--shadow-report",
        action="store_true",
        help="Aggregate persisted critic shadow-mode runs instead of fixture cases.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum persisted runs to include in --shadow-report mode.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.shadow_report:
        runs = _load_shadow_runs(limit=args.limit)
        steps = _load_shadow_steps([run.run_id for run in runs])
        report = build_shadow_report(runs, steps)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
            return 0
        print(render_shadow_markdown(report), end="")
        return 0

    cases = [load_critic_case(path) for path in critic_case_paths(args.fixture)]
    result = evaluate_suite(cases)
    if args.live:
        _require_live_confirmation(args.confirm_cost)
        result = _with_live_note(result)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(render_suite_markdown(result), end="")
    return 0 if result.passed else 1


def _require_live_confirmation(confirm_cost: str) -> None:
    if confirm_cost.lower() != "yes":
        raise RuntimeError("Live evaluation requires --confirm-cost yes.")
    if not get_settings().VERTEX_AI_PROJECT:
        raise RuntimeError("Live evaluation requires VERTEX_AI_PROJECT.")


def _with_live_note(result: CriticSuiteResult) -> CriticSuiteResult:
    live_note = (
        "Live mode currently reuses the frozen offline critic fixtures; no live-only critic golden cases are defined yet."
    )
    return CriticSuiteResult(
        total_cases=result.total_cases,
        passed_cases=result.passed_cases,
        failed_cases=result.failed_cases,
        passed=result.passed,
        cases=result.cases,
        notes=(*result.notes, live_note),
    )


def _load_shadow_runs(*, limit: int) -> list[AgentRun]:
    engine = get_engine(os.environ.get("DATABASE_URL"))
    statement = (
        select(AgentRun)
        .where(AgentRun.critic_effect.is_not(None))
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
        AgentRunStep.agent_name == "critic_guardrail",
    )
    with get_session(engine) as session:
        return list(session.exec(statement).all())


if __name__ == "__main__":
    sys.exit(main())
