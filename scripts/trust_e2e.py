#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from agents.config import AGENTIC_PIPELINE_MODE, load_agent_config
from agents.orchestration.workflow import AgenticAnalysisWorkflow, GoogleWorkflowError, load_trace
from app.db.repository import get_latest_macro_daily, get_latest_technical_feature, list_active_securities
from app.db.session import get_engine
from app.ingestion.universe import seed_universe
from app.rag.embedding import build_configured_embedding_client
from app.schemas.analyze import AnalysisTrace, AnalyzeRequest, AnalyzeResponse
from app.services.analyzer import analyze_request
from app.settings import Settings, get_settings
from app.trust.memo_checks import TechnicalIndicatorSnapshot, check_memo_consistency
from app.trust.invariants import (
    CRITIC_OFF_ALLOWED_SKIPS,
    check_critic_shadow_persistence,
    evaluate_agentic_invariants,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MATRIX_PATH = REPO_ROOT / "scripts" / "trust_tickers.json"
DEFAULT_REPORT_DIR = REPO_ROOT / "reports" / "trust"
CRITERIA_PATH = REPO_ROOT / "docs" / "trust" / "criteria.md"
CONSISTENCY_THRESHOLDS = {
    "risk_number_equality_rate": 1.0,
    "decision_upgrade_count": 0,
    "unstable_ticker_count": 1,
    "confidence_stddev_max": 0.05,
}


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
    parser.add_argument("--repeat", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    matrix_path = Path(args.matrix).resolve()
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    if args.repeat < 1:
        raise ValueError("--repeat must be >= 1")

    matrix_payload = _load_matrix(matrix_path)
    settings = get_settings()
    critic_mode = settings.CRITIC_MODE.strip().lower()
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
        "criteria_path": str(CRITERIA_PATH.relative_to(REPO_ROOT)),
        "critic_mode": critic_mode,
        "pipeline_mode": AGENTIC_PIPELINE_MODE,
        "repeat_count": args.repeat,
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

    for iteration in range(1, args.repeat + 1):
        for matrix_ticker in tickers:
            linear_response = analyze_request(
                AnalyzeRequest(ticker=matrix_ticker.ticker, as_of_date=as_of_date),
                engine=engine,
            )
            run_report: dict[str, Any] = {
                "iteration": iteration,
                "ticker": matrix_ticker.ticker,
                "sector": matrix_ticker.sector,
                "expected_case": matrix_ticker.expected_case,
                "notes": matrix_ticker.notes,
                "linear": _response_summary(linear_response),
                "agentic": None,
                "trace": None,
                "fundamental_agent_effect": None,
                "violations": [],
                "status": "blocked" if workflow is None else "pending",
                "error": workflow_error,
            }

            if workflow is not None:
                try:
                    with _settings_override(CRITIC_MODE=critic_mode):
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
                        allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS if critic_mode == "off" else None,
                    )
                    if critic_mode == "shadow":
                        violations.extend(check_critic_shadow_persistence(agentic_response, persisted_trace))
                    memo_text = _memo_text(persisted_trace)
                    run_report["agentic"] = _response_summary(agentic_response)
                    run_report["trace"] = _trace_summary(persisted_trace)
                    run_report["memo_checks"] = _memo_check_summary(
                        memo_text=memo_text,
                        response=agentic_response,
                        ticker=matrix_ticker.ticker,
                        engine=engine,
                    )
                    run_report["fundamental_agent_effect"] = _fundamental_effect_summary(
                        persisted_trace.fundamental_agent_effect or agentic_response.fundamental_agent_effect
                    )
                    run_report["fundamental_citation_resolution"] = _fundamental_citation_resolution_summary(
                        persisted_trace
                    )
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

    report["summary"] = _build_summary(report["runs"], repeat_count=args.repeat)
    report_prefix = "trust-consistency" if args.repeat > 1 else "trust-e2e"
    report_path = report_dir / f"{report_prefix}-{critic_mode}-{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    _print_summary(report["runs"], report_path)
    consistency_status = report["summary"].get("consistency", {}).get("status")
    if consistency_status == "failed":
        exit_code = 1
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


def _build_summary(runs: list[dict[str, Any]], *, repeat_count: int) -> dict[str, Any]:
    counts = {"passed": 0, "failed": 0, "blocked": 0}
    memo_failure_count = 0
    memo_warning_count = 0
    citation_unknown_count = 0
    for run in runs:
        counts[run["status"]] += 1
        memo_checks = run.get("memo_checks") or {}
        memo_failure_count += int(memo_checks.get("failure_count", 0) or 0)
        memo_warning_count += int(memo_checks.get("warning_count", 0) or 0)
        citation_summary = run.get("fundamental_citation_resolution") or {}
        citation_unknown_count += int(citation_summary.get("unknown_id_count", 0) or 0)
    counts["total"] = len(runs)
    counts["memo_failure_count"] = memo_failure_count
    counts["memo_warning_count"] = memo_warning_count
    counts["fundamental_unknown_citation_id_count"] = citation_unknown_count
    counts["consistency"] = _build_consistency_summary(runs, repeat_count=repeat_count)
    return counts


def _build_consistency_summary(runs: list[dict[str, Any]], *, repeat_count: int) -> dict[str, Any]:
    if repeat_count < 2:
        return {"status": "not_requested", "thresholds": CONSISTENCY_THRESHOLDS}

    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_ticker.setdefault(str(run["ticker"]), []).append(run)

    ticker_summaries: list[dict[str, Any]] = []
    unstable_ticker_count = 0
    decision_upgrade_count = 0
    risk_match_count = 0
    comparable_run_count = 0
    blocked_tickers: list[str] = []
    confidence_stddev_values: list[float] = []

    for ticker, ticker_runs in sorted(by_ticker.items()):
        completed_runs = [run for run in ticker_runs if isinstance(run.get("agentic"), dict)]
        blocked_runs = [run for run in ticker_runs if run["status"] == "blocked"]
        if blocked_runs:
            blocked_tickers.append(ticker)
        decisions = [str(run["agentic"]["decision"]) for run in completed_runs]
        flip_detected = len(set(decisions)) > 1
        if flip_detected:
            unstable_ticker_count += 1
        confidences = [float(run["agentic"]["confidence"]) for run in completed_runs]
        confidence_stddev = statistics.stdev(confidences) if len(confidences) > 1 else 0.0
        confidence_stddev_values.append(confidence_stddev)

        fundamental_adjustments = sorted(
            {
                int(effect["raw_adjustment"])
                for run in completed_runs
                if isinstance((effect := run.get("fundamental_agent_effect")), dict)
                and effect.get("raw_adjustment") is not None
            }
        )
        decision_counts: dict[str, int] = {}
        for decision in decisions:
            decision_counts[decision] = decision_counts.get(decision, 0) + 1

        for run in completed_runs:
            comparable_run_count += 1
            if _risk_fields_match(run.get("linear"), run.get("agentic")):
                risk_match_count += 1
            if _decision_rank(run.get("agentic", {}).get("decision")) > _decision_rank(run.get("linear", {}).get("decision")):
                decision_upgrade_count += 1

        ticker_summaries.append(
            {
                "ticker": ticker,
                "expected_runs": len(ticker_runs),
                "completed_runs": len(completed_runs),
                "blocked_runs": len(blocked_runs),
                "decision_counts": decision_counts,
                "flip_detected": flip_detected,
                "mean_confidence": statistics.mean(confidences) if confidences else None,
                "confidence_stddev": confidence_stddev if confidences else None,
                "fundamental_adjustments": fundamental_adjustments,
            }
        )

    risk_number_equality_rate = (
        risk_match_count / comparable_run_count if comparable_run_count else None
    )
    max_confidence_stddev = max(confidence_stddev_values, default=0.0)
    failures: list[str] = []
    if risk_number_equality_rate is not None and risk_number_equality_rate < CONSISTENCY_THRESHOLDS["risk_number_equality_rate"]:
        failures.append("risk_number_equality_below_threshold")
    if decision_upgrade_count > CONSISTENCY_THRESHOLDS["decision_upgrade_count"]:
        failures.append("decision_upgrade_detected")
    if unstable_ticker_count > CONSISTENCY_THRESHOLDS["unstable_ticker_count"]:
        failures.append("flip_rate_exceeded")
    if max_confidence_stddev > CONSISTENCY_THRESHOLDS["confidence_stddev_max"]:
        failures.append("confidence_variance_exceeded")

    if blocked_tickers:
        status = "blocked"
    elif failures:
        status = "failed"
    else:
        status = "passed"

    return {
        "status": status,
        "thresholds": CONSISTENCY_THRESHOLDS,
        "blocked_tickers": blocked_tickers,
        "comparable_run_count": comparable_run_count,
        "risk_number_equality_rate": risk_number_equality_rate,
        "decision_upgrade_count": decision_upgrade_count,
        "unstable_ticker_count": unstable_ticker_count,
        "max_confidence_stddev": max_confidence_stddev,
        "failures": failures,
        "tickers": ticker_summaries,
    }


def _print_summary(runs: list[dict[str, Any]], report_path: Path) -> None:
    print("ticker\tsector\texpected\tstatus\tdecision\tviolations\tmemo_failures\tmemo_warnings\tcitation_unknown_ids")
    for run in runs:
        decision = "-"
        if isinstance(run.get("agentic"), dict):
            decision = str(run["agentic"].get("decision"))
        memo_checks = run.get("memo_checks") or {}
        citation_summary = run.get("fundamental_citation_resolution") or {}
        print(
            "\t".join(
                [
                    str(run["ticker"]),
                    str(run["sector"]),
                    str(run["expected_case"]),
                    str(run["status"]),
                    decision,
                    str(len(run["violations"])),
                    str(memo_checks.get("failure_count", "-")),
                    str(memo_checks.get("warning_count", "-")),
                    str(citation_summary.get("unknown_id_count", "-")),
                ]
            )
        )
    print(f"report\t{report_path}")


def _risk_fields_match(linear: dict[str, Any] | None, agentic: dict[str, Any] | None) -> bool:
    if not isinstance(linear, dict) or not isinstance(agentic, dict):
        return False
    return all(
        linear.get(field_name) == agentic.get(field_name)
        for field_name in ("entry_range", "stop_loss", "take_profit", "risk_reward", "position_size_eur")
    )


def _decision_rank(decision: Any) -> int:
    return {"no_trade": 0, "watchlist": 1, "trade": 2}.get(str(decision), -1)


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


def _memo_text(trace: AnalysisTrace) -> str:
    for step in reversed(trace.steps):
        if step.agent_name != "decision_synthesizer" or not isinstance(step.output, dict):
            continue
        revision_text = step.output.get("revision_text")
        if isinstance(revision_text, str) and revision_text.strip():
            return revision_text
        text = step.output.get("text")
        if isinstance(text, str) and text.strip():
            return text
    return ""


def _fundamental_effect_summary(effect: Any) -> dict[str, Any] | None:
    if effect is None:
        return None
    return {
        "mode": effect.mode,
        "accepted": effect.accepted,
        "assessment_status": effect.assessment_status,
        "reason_codes": list(effect.reason_codes),
        "raw_adjustment": effect.raw_adjustment,
        "applied_adjustment": effect.applied_adjustment,
        "baseline_decision": effect.baseline_decision,
        "final_decision": effect.final_decision,
    }


def _memo_check_summary(
    *,
    memo_text: str,
    response: AnalyzeResponse,
    ticker: str,
    engine,
) -> dict[str, Any]:
    if not memo_text.strip():
        return {
            "status": "unavailable",
            "failure_count": 0,
            "warning_count": 0,
            "issues": [],
            "reason": "memo_text_missing",
        }
    result = check_memo_consistency(
        memo_text,
        response,
        _technical_indicator_snapshot(ticker, engine),
    )
    return {
        "status": "passed" if result.passed else "failed",
        "failure_count": result.failure_count,
        "warning_count": result.warning_count,
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "field_name": issue.field_name,
                "message": issue.message,
                "claim_text": issue.claim_text,
                "expected": issue.expected,
                "actual": issue.actual,
            }
            for issue in result.issues
        ],
    }


def _technical_indicator_snapshot(ticker: str, engine) -> TechnicalIndicatorSnapshot | None:
    technical_feature = get_latest_technical_feature(ticker, engine=engine)
    macro_row = get_latest_macro_daily(engine=engine)
    if technical_feature is None and macro_row is None:
        return None
    return TechnicalIndicatorSnapshot(
        rsi_14=float(technical_feature.rsi_14) if technical_feature and technical_feature.rsi_14 is not None else None,
        sma_50=float(technical_feature.sma_50) if technical_feature and technical_feature.sma_50 is not None else None,
        sma_200=float(technical_feature.sma_200) if technical_feature and technical_feature.sma_200 is not None else None,
        vix=float(macro_row.vix) if macro_row and macro_row.vix is not None else None,
        volume_trend=(
            float(technical_feature.volume_trend)
            if technical_feature and technical_feature.volume_trend is not None
            else None
        ),
    )


def _fundamental_citation_resolution_summary(trace: AnalysisTrace) -> dict[str, Any]:
    builder_step = _trace_step(trace, "fundamental_context_builder")
    analyst_step = _trace_step(trace, "fundamental_analyst")
    if builder_step is None or not isinstance(builder_step.output, dict):
        return {
            "status": "unavailable",
            "unknown_id_count": 0,
            "reason_codes": [],
            "reason": "fundamental_context_missing",
        }
    if analyst_step is None:
        return {
            "status": "unavailable",
            "unknown_id_count": 0,
            "reason_codes": [],
            "reason": "fundamental_step_missing",
        }
    reason_codes = _unknown_citation_reason_codes(analyst_step)
    return {
        "status": "flagged" if reason_codes else "confirmed_zero",
        "unknown_id_count": len(reason_codes),
        "reason_codes": reason_codes,
        "context_hash": builder_step.output.get("context_hash"),
        "chunk_count": builder_step.output.get("chunk_count"),
    }


def _trace_step(trace: AnalysisTrace, agent_name: str):
    for step in trace.steps:
        if step.agent_name == agent_name:
            return step
    return None


def _unknown_citation_reason_codes(step) -> list[str]:
    if not isinstance(step.output, dict):
        return []
    detail = step.output.get("detail")
    if not isinstance(detail, str):
        return []
    return [
        reason_code
        for reason_code in detail.split(",")
        if reason_code in {"unknown_metric_citation", "unknown_chunk_citation"}
    ]


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
