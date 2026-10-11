#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from agents.config import AGENTIC_PIPELINE_MODE, load_agent_config
from agents.orchestration.workflow import AgenticAnalysisWorkflow, GoogleWorkflowError, load_trace
from app.db.repository import list_active_securities
from app.db.session import get_engine
from app.ingestion.universe import seed_universe
from app.rag.embedding import build_configured_embedding_client
from app.schemas.analyze import AnalysisTrace, AnalyzeRequest, AnalyzeResponse
from app.services.analyzer import analyze_request
from app.settings import Settings, get_settings
from app.trust.invariants import CRITIC_OFF_ALLOWED_SKIPS, evaluate_agentic_invariants

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MATRIX_PATH = REPO_ROOT / "scripts" / "trust_tickers.json"
DEFAULT_REPORT_DIR = REPO_ROOT / "reports" / "trust"


@dataclass(frozen=True)
class MatrixTicker:
    ticker: str
    sector: str
    expected_case: str
    notes: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX_PATH))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    matrix_path = Path(args.matrix).resolve()
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    matrix_payload = _load_matrix(matrix_path)
    as_of_date = date.fromisoformat(str(matrix_payload["as_of_date"]))
    tickers = [
        MatrixTicker(
            ticker=str(item["ticker"]),
            sector=str(item["sector"]),
            expected_case=str(item["expected_case"]),
            notes=str(item["notes"]),
        )
        for item in matrix_payload["tickers"]
    ]

    engine = get_engine()
    seeded = seed_universe(engine=engine)
    active_universe_size = len(list_active_securities(engine=engine))
    generated_at = datetime.now(timezone.utc)

    report: dict[str, Any] = {
        "generated_at": generated_at.isoformat(),
        "as_of_date": as_of_date.isoformat(),
        "matrix_path": str(matrix_path.relative_to(REPO_ROOT)),
        "report_dir": str(report_dir.relative_to(REPO_ROOT)),
        "critic_mode": "off",
        "pipeline_mode": AGENTIC_PIPELINE_MODE,
        "seeded_universe_inserted_count": seeded,
        "active_universe_size": active_universe_size,
        "credential_probe": _credential_probe(),
        "selection_basis": matrix_payload.get("selection_basis", {}),
        "runs": [],
        "summary": {},
    }

    exit_code = 0
    workflow_error: dict[str, Any] | None = None

    try:
        embedding_client = build_configured_embedding_client()
    except Exception as exc:
        embedding_client = None
        workflow_error = _serialize_error(exc)
        exit_code = 1

    workflow = None
    if embedding_client is not None:
        workflow = AgenticAnalysisWorkflow(
            load_agent_config(Settings(_env_file=None, PIPELINE_MODE=AGENTIC_PIPELINE_MODE)),
            engine=engine,
            embedding_client=embedding_client,
        )

    for matrix_ticker in tickers:
        linear_response = analyze_request(
            AnalyzeRequest(ticker=matrix_ticker.ticker, as_of_date=as_of_date),
            engine=engine,
        )
        run_report: dict[str, Any] = {
            "ticker": matrix_ticker.ticker,
            "sector": matrix_ticker.sector,
            "expected_case": matrix_ticker.expected_case,
            "notes": matrix_ticker.notes,
            "linear": _response_summary(linear_response),
            "agentic": None,
            "trace": None,
            "violations": [],
            "status": "blocked" if workflow is None else "pending",
            "error": workflow_error,
        }

        if workflow is not None:
            try:
                with _settings_override(CRITIC_MODE="off"):
                    agentic_response = workflow.analyze(
                        matrix_ticker.ticker,
                        request=AnalyzeRequest(ticker=matrix_ticker.ticker, as_of_date=as_of_date),
                    )
                persisted_trace = load_trace(agentic_response.trace_id, engine=engine) or agentic_response.trace
                if persisted_trace is None:
                    raise RuntimeError(f"persisted trace not found for run_id={agentic_response.trace_id}")
                violations = evaluate_agentic_invariants(
                    deterministic_response=linear_response,
                    agentic_response=agentic_response,
                    trace=persisted_trace,
                    allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
                )
                run_report["agentic"] = _response_summary(agentic_response)
                run_report["trace"] = _trace_summary(persisted_trace)
                run_report["violations"] = [violation.as_dict() for violation in violations]
                run_report["status"] = "passed" if not violations else "failed"
                run_report["error"] = None
                if violations:
                    exit_code = 1
            except Exception as exc:
                run_report["status"] = "blocked"
                run_report["error"] = _serialize_error(exc)
                exit_code = 1
        report["runs"].append(run_report)

    report["summary"] = _build_summary(report["runs"])
    report_path = report_dir / f"trust-e2e-{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    _print_summary(report["runs"], report_path)
    return exit_code


def _load_matrix(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ticker matrix must be a JSON object.")
    return payload


def _response_summary(response: AnalyzeResponse) -> dict[str, Any]:
    diagnosis = None
    if response.diagnosis is not None:
        diagnosis = {
            "stage": response.diagnosis.stage,
            "rule_id": response.diagnosis.rule_id,
            "detail": response.diagnosis.detail,
            "checklist_score": response.diagnosis.checklist_score,
            "missing_data": list(response.diagnosis.missing_data),
        }
    return {
        "ticker": response.ticker,
        "decision": response.decision,
        "confidence": response.confidence,
        "entry_range": list(response.entry_range) if response.entry_range is not None else None,
        "stop_loss": response.stop_loss,
        "take_profit": list(response.take_profit) if response.take_profit is not None else None,
        "risk_reward": response.risk_reward,
        "position_size_eur": response.position_size_eur,
        "warnings": list(response.warnings),
        "reasons": list(response.reasons),
        "trace_id": response.trace_id,
        "diagnosis": diagnosis,
    }


def _trace_summary(trace: AnalysisTrace) -> dict[str, Any]:
    return {
        "run_id": trace.run_id,
        "final_decision": trace.final_decision,
        "warnings": list(trace.warnings),
        "entered_agent_layer": trace.entered_agent_layer,
        "adk_event_count": trace.adk_event_count,
        "observed_agents": list(trace.observed_agents),
        "steps": [
            {
                "sequence": step.sequence,
                "agent_name": step.agent_name,
                "status": step.status,
                "tool_calls": list(step.tool_calls),
                "latency_ms": step.latency_ms,
                "token_usage": dict(step.token_usage),
                "retries": step.retries,
                "skip_reason": step.skip_reason,
                "output_keys": sorted(step.output.keys()) if isinstance(step.output, dict) else [],
                "output_reason": step.output.get("reason") if isinstance(step.output, dict) else None,
            }
            for step in trace.steps
        ],
    }


def _build_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"passed": 0, "failed": 0, "blocked": 0}
    for run in runs:
        counts[run["status"]] += 1
    counts["total"] = len(runs)
    return counts


def _print_summary(runs: list[dict[str, Any]], report_path: Path) -> None:
    print("ticker\tsector\texpected\tstatus\tdecision\tviolations")
    for run in runs:
        decision = "-"
        if isinstance(run.get("agentic"), dict):
            decision = str(run["agentic"].get("decision"))
        print(
            "\t".join(
                [
                    str(run["ticker"]),
                    str(run["sector"]),
                    str(run["expected_case"]),
                    str(run["status"]),
                    decision,
                    str(len(run["violations"])),
                ]
            )
        )
    print(f"report\t{report_path}")


def _serialize_error(exc: Exception) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "is_google_workflow_error": isinstance(exc, GoogleWorkflowError),
    }


def _credential_probe() -> dict[str, Any]:
    candidate = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    return {
        "path": str(candidate),
        "exists": candidate.exists(),
        "is_file": candidate.is_file(),
        "is_dir": candidate.is_dir(),
    }


@contextmanager
def _settings_override(**env_updates: str) -> Iterator[None]:
    original_values = {name: os.environ.get(name) for name in env_updates}
    try:
        for name, value in env_updates.items():
            os.environ[name] = value
        get_settings.cache_clear()
        yield
    finally:
        for name, original_value in original_values.items():
            if original_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original_value
        get_settings.cache_clear()


if __name__ == "__main__":
    sys.exit(main())
