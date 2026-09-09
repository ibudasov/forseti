#!/usr/bin/env python
"""Replay a recorded or frozen agent run without contacting a live model."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlmodel import Session
from sqlmodel import SQLModel

from agents.config import load_agent_config
from agents.orchestration.cassette_runner import build_cassette_runner_factory
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.db.session import get_engine
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from tests.fixtures.golden.loader import deterministic_fields, load_golden_case

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"
DEFAULT_DEBUG_DIR = Path("/tmp/forseti-llm-io")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--run-id")
    selection.add_argument("--golden-case")
    parser.add_argument("--json", action="store_true", help="Print the replayed AnalyzeResponse as JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.golden_case:
        response, expected = replay_golden_case(args.golden_case)
    else:
        response, expected = replay_recorded_run(args.run_id)

    if args.json:
        print(json.dumps(response.model_dump(mode="json"), indent=2))

    mismatches = compare_expected_fields(response, expected)
    if mismatches:
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    return 0


def replay_golden_case(case_name: str) -> tuple[AnalyzeResponse, dict[str, Any]]:
    test_database_url = os.environ.get("TEST_DATABASE_URL")
    if not test_database_url:
        raise RuntimeError("--golden-case requires TEST_DATABASE_URL so fixture seeding stays isolated.")
    case = load_golden_case(case_name)
    engine = get_engine(test_database_url)
    with engine.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.commit()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        case.seed(session)

    if case.cassette_path is None:
        raise RuntimeError(f"Golden case {case_name} is missing a cassette directory.")
    workflow = AgenticAnalysisWorkflow(
        load_agent_config(),
        engine=engine,
        runner_factory=build_cassette_runner_factory(case.cassette_path),
    )
    response = workflow.analyze(
        case.ticker,
        request=AnalyzeRequest(ticker=case.ticker, as_of_date=case.today),
    )
    return response, dict(case.expected)


def replay_recorded_run(run_id: str) -> tuple[AnalyzeResponse, dict[str, Any]]:
    debug_directory = Path(os.environ.get("DEBUG_LLM_IO_DIR", DEFAULT_DEBUG_DIR))
    run_directory = debug_directory / run_id
    run_config = _load_json(run_directory / "000-run-config.json")
    _load_json(run_directory / "002-user-message.json")
    summary = _load_json(run_directory / "999-summary.json")

    ticker = str(run_config["ticker"])
    request = AnalyzeRequest(ticker=ticker, as_of_date=_recorded_today(run_config))
    workflow = AgenticAnalysisWorkflow(
        _replay_config(run_config),
        engine=get_engine(),
        runner_factory=build_cassette_runner_factory(run_directory),
    )
    response = workflow.analyze(ticker, request=request)
    expected = summary.get("deterministic_fields")
    if not isinstance(expected, dict):
        raise RuntimeError(
            f"Recorded run {run_id} is missing deterministic_fields in {run_directory / '999-summary.json'}."
        )
    return response, expected


def compare_expected_fields(response: AnalyzeResponse, expected: dict[str, Any]) -> list[str]:
    actual = deterministic_fields(response)
    normalized_expected = dict(expected)
    normalized_expected["warnings"] = sorted(set(normalized_expected.get("warnings") or []))
    normalized_expected["reasons"] = list(normalized_expected.get("reasons") or [])
    mismatches = []
    for field_name, actual_value in actual.items():
        expected_value = normalized_expected.get(field_name)
        if actual_value != expected_value:
            mismatches.append(
                f"{field_name}: expected {expected_value!r}, got {actual_value!r}"
            )
    return mismatches


def _recorded_today(run_config: dict[str, Any]) -> date:
    recorded_today = run_config.get("today")
    if recorded_today:
        return date.fromisoformat(str(recorded_today))

    started_at = run_config.get("started_at")
    if not started_at:
        raise RuntimeError("Recorded run is missing both 'today' and 'started_at' in 000-run-config.json.")
    return date.fromisoformat(str(started_at)[:10])


def _replay_config(run_config: dict[str, Any]):
    config = load_agent_config()
    return replace(
        config,
        pipeline_mode=str(run_config.get("pipeline_mode", config.pipeline_mode)),
        model_name=str(run_config.get("model", config.model_name)),
        temperature=float(run_config.get("temperature", config.temperature)),
        timeout_seconds=float(run_config.get("timeout_seconds", config.timeout_seconds)),
        max_retries=int(run_config.get("max_retries", config.max_retries)),
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main())
