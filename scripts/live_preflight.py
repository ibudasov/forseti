#!/usr/bin/env python
from __future__ import annotations

import argparse
import html
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.config import AGENTIC_PIPELINE_MODE, LINEAR_PIPELINE_MODE, load_agent_config
from app.db.session import get_engine
from app.schemas.analyze import AnalysisTrace, AnalyzeRequest, AnalyzeResponse, TraceStep
from app.services.pipeline import AnalysisPipeline, select_pipeline
from app.services.token_pricing import estimate_usage_cost_usd
from app.settings import Settings, get_settings
from app.trust.invariants import CRITIC_OFF_ALLOWED_SKIPS, evaluate_agentic_invariants

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MATRIX_PATH = REPO_ROOT / "scripts" / "trust_tickers.json"
DEFAULT_REPORT_DIR = REPO_ROOT / "reports" / "preflight"
DEFAULT_TICKER_COUNT = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a confirmed live linear/agentic pre-flight.")
    parser.add_argument("--confirm-cost", required=True, choices=["yes"])
    parser.add_argument("--tickers", default=None)
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX_PATH))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    tickers = _requested_tickers(args.tickers, Path(args.matrix))
    engine = get_engine()
    linear_pipeline = _build_pipeline(LINEAR_PIPELINE_MODE, engine, settings)
    agentic_pipeline = _build_pipeline(AGENTIC_PIPELINE_MODE, engine, settings)
    generated_at = datetime.now(timezone.utc)

    report = run_preflight(
        tickers,
        linear_pipeline=linear_pipeline,
        agentic_pipeline=agentic_pipeline,
        model_name=settings.GEMINI_MODEL,
        critic_mode=settings.CRITIC_MODE.strip().lower(),
        generated_at=generated_at,
    )
    report["agent_modes"] = {
        "pipeline": settings.PIPELINE_MODE,
        "fundamental": settings.FUNDAMENTAL_AGENT_MODE,
        "technical": settings.TECHNICAL_AGENT_MODE,
        "critic": settings.CRITIC_MODE,
        "synthesizer_contract": settings.SYNTHESIZER_CONTRACT,
    }
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"{timestamp}.json"
    markdown_path = report_dir / f"{timestamp}.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return 0 if all(run["status"] == "passed" for run in report["runs"]) else 1


def _build_pipeline(mode: str, engine: Any, settings: Settings) -> AnalysisPipeline:
    pipeline_settings = settings.model_copy(update={"PIPELINE_MODE": mode})
    config = load_agent_config(pipeline_settings)
    return select_pipeline(mode, config, override_allowed=True, engine=engine)


def _requested_tickers(tickers: str | None, matrix_path: Path) -> list[str]:
    if tickers is None:
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        selected = [str(item["ticker"]) for item in matrix.get("tickers", [])[:DEFAULT_TICKER_COUNT]]
    else:
        selected = [ticker.strip() for ticker in tickers.split(",") if ticker.strip()]
    normalized = [AnalyzeRequest(ticker=ticker).ticker for ticker in selected]
    if not normalized:
        raise ValueError("At least one ticker is required.")
    if len(normalized) != len(set(normalized)):
        raise ValueError("Ticker list must not contain duplicates.")
    return normalized


def run_preflight(
    tickers: list[str],
    *,
    linear_pipeline: AnalysisPipeline,
    agentic_pipeline: AnalysisPipeline,
    model_name: str,
    critic_mode: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = {
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "model_name": model_name,
        "runs": [],
    }
    for ticker in tickers:
        request = AnalyzeRequest(ticker=ticker)
        linear, linear_latency, linear_error = _analyze(linear_pipeline, request)
        agentic, agentic_latency, agentic_error = _analyze(agentic_pipeline, request)
        run: dict[str, Any] = {
            "ticker": request.ticker,
            "linear": _analysis_summary(linear, linear_latency, model_name, is_linear=True),
            "agentic": _analysis_summary(agentic, agentic_latency, model_name),
            "invariants": None,
            "errors": {
                mode: error
                for mode, error in (("linear", linear_error), ("agentic", agentic_error))
                if error is not None
            },
        }
        if linear is not None and agentic is not None and agentic.trace is not None:
            allowed_skips = CRITIC_OFF_ALLOWED_SKIPS if critic_mode == "off" else None
            violations = evaluate_agentic_invariants(
                deterministic_response=linear,
                agentic_response=agentic,
                trace=agentic.trace,
                allowed_skip_reasons=allowed_skips,
            )
            run["invariants"] = {
                "passed": not violations,
                "results": [violation.as_dict() for violation in violations],
            }
        elif agentic is not None:
            run["errors"]["invariants"] = "Agentic response did not include an analysis trace."
        run["status"] = _run_status(run)
        report["runs"].append(run)
    report["summary"] = _aggregate_summary(report["runs"])
    return report


def _aggregate_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    analyses = [run["agentic"] for run in runs if run["agentic"] is not None]
    latencies = sorted(analysis["total_latency_ms"] for analysis in analyses)
    costs = [
        analysis["estimated_cost_usd"]
        for analysis in analyses
        if analysis["estimated_cost_usd"] is not None
    ]
    token_usage: dict[str, int] = {}
    for analysis in analyses:
        for token_name, token_count in analysis["token_usage"].items():
            token_usage[token_name] = token_usage.get(token_name, 0) + token_count
    return {
        "agentic_run_count": len(analyses),
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "token_usage": token_usage,
        "estimated_cost_usd": {
            "total": sum(costs),
            "pending_run_count": len(analyses) - len(costs),
        },
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * percentile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values) - 1)
    fraction = position - lower_index
    return values[lower_index] + (values[upper_index] - values[lower_index]) * fraction


def _analyze(
    pipeline: AnalysisPipeline,
    request: AnalyzeRequest,
) -> tuple[AnalyzeResponse | None, float, str | None]:
    started_at = time.monotonic()
    try:
        response = pipeline.analyze(request)
    except Exception as exc:
        return None, (time.monotonic() - started_at) * 1000, f"{type(exc).__name__}: {exc}"
    return response, (time.monotonic() - started_at) * 1000, None


def _analysis_summary(
    response: AnalyzeResponse | None,
    elapsed_ms: float,
    model_name: str,
    *,
    is_linear: bool = False,
) -> dict[str, Any] | None:
    if response is None:
        return None
    trace = response.trace
    token_usage = dict(trace.token_usage) if trace is not None else {}
    estimated_cost = (
        0.0
        if is_linear
        else estimate_usage_cost_usd(model_name, token_usage)
    )
    steps = trace.steps if trace is not None else []
    return {
        "decision": response.decision,
        "confidence": response.confidence,
        "warnings": list(response.warnings),
        "total_latency_ms": elapsed_ms,
        "trace_latency_ms": trace.total_latency_ms if trace is not None else None,
        "stage_latency_ms": _stage_latency(steps) if steps else {"linear_analysis": elapsed_ms},
        "token_usage": token_usage,
        "stage_token_usage": _stage_token_usage(steps),
        "estimated_cost_usd": estimated_cost,
        "cost_status": "estimated" if estimated_cost is not None else "pending",
        "degraded_flags": _degraded_flags(trace),
    }


def _stage_latency(steps: list[TraceStep]) -> dict[str, float]:
    stage_latency: dict[str, float] = {}
    for step in steps:
        stage_latency[step.agent_name] = stage_latency.get(step.agent_name, 0.0) + step.latency_ms
    return stage_latency


def _stage_token_usage(steps: list[TraceStep]) -> dict[str, dict[str, int]]:
    stage_token_usage: dict[str, dict[str, int]] = {}
    for step in steps:
        token_usage = stage_token_usage.setdefault(step.agent_name, {})
        for token_name, token_count in step.token_usage.items():
            token_usage[token_name] = token_usage.get(token_name, 0) + token_count
    return stage_token_usage


def _degraded_flags(trace: AnalysisTrace | None) -> dict[str, Any]:
    if trace is None:
        return {
            "unparsable_synthesis": False,
            "guardrail_rejected": False,
            "agent_fallbacks": [],
            "degraded_agents": [],
        }
    reasons = list(trace.warnings)
    fallback_agents = []
    degraded_agents = []
    for step in trace.steps:
        output = step.output if isinstance(step.output, dict) else {}
        reason = str(output.get("reason", ""))
        reasons.append(reason)
        if output.get("used_fallback") is True or output.get("fallback_used") is True:
            fallback_agents.append(step.agent_name)
        if step.status == "degraded":
            degraded_agents.append(step.agent_name)
    synthesizer_effect = trace.synthesizer_effect
    if synthesizer_effect is not None and synthesizer_effect.fallback_used:
        fallback_agents.append("decision_synthesizer")
    all_reasons = " ".join(reasons)
    return {
        "unparsable_synthesis": "unparsable_synthesis" in all_reasons,
        "guardrail_rejected": "guardrail_rejected" in all_reasons,
        "agent_fallbacks": sorted(set(fallback_agents)),
        "degraded_agents": sorted(set(degraded_agents)),
    }


def _run_status(run: dict[str, Any]) -> str:
    if run["errors"]:
        return "blocked"
    if run["invariants"] is None or not run["invariants"]["passed"]:
        return "failed"
    return "passed"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Live pre-flight",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Model: {_markdown_cell(report['model_name'])}",
        f"- Agent modes: {_markdown_cell(json.dumps(report.get('agent_modes', {}), sort_keys=True))}",
        "",
        "| Ticker | Status | Linear | Agentic | Warnings | Degraded/fallback | "
        "Invariants | Agentic latency (ms) | Tokens | Estimated cost (USD) |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for run in report["runs"]:
        agentic = run["agentic"] or {}
        flags = agentic.get("degraded_flags", {})
        linear_warnings = (run["linear"] or {}).get("warnings", [])
        agentic_warnings = agentic.get("warnings", [])
        warnings = "; ".join(
            item
            for item in (
                f"linear: {', '.join(linear_warnings)}" if linear_warnings else "",
                f"agentic: {', '.join(agentic_warnings)}" if agentic_warnings else "",
            )
            if item
        ) or "—"
        warnings = _markdown_cell(warnings)
        degradation = [
            flag_name
            for flag_name in ("unparsable_synthesis", "guardrail_rejected")
            if flags.get(flag_name)
        ]
        degradation.extend(flags.get("agent_fallbacks", []))
        degraded_agents = flags.get("degraded_agents", [])
        if degraded_agents:
            degradation.append(f"degraded: {', '.join(degraded_agents)}")
        degradation_summary = _markdown_cell(", ".join(degradation) or "—")
        invariant_status = "not run"
        if run["invariants"] is not None:
            invariant_status = "passed" if run["invariants"]["passed"] else "failed"
        cost = agentic.get("estimated_cost_usd")
        cost_text = "pending" if cost is None else f"{cost:.6f}"
        tokens = agentic.get("token_usage", {}).get("total_token_count", 0)
        lines.append(
            f"| {_markdown_cell(run['ticker'])} | {run['status']} | "
            f"{_decision(run['linear'])} | {_decision(run['agentic'])} | "
            f"{warnings} | {degradation_summary} | {invariant_status} | "
            f"{agentic.get('total_latency_ms', 0.0):.1f} | {tokens} | {cost_text} |"
        )
        if agentic.get("stage_latency_ms"):
            stages = ", ".join(
                f"{_markdown_cell(name)}: {latency:.1f} ms"
                for name, latency in agentic["stage_latency_ms"].items()
            )
            lines.append(f"\n**{_markdown_cell(run['ticker'])} stage latency:** {stages}")
        if run["invariants"] and run["invariants"]["results"]:
            invariant_codes = ", ".join(
                item["code"] for item in run["invariants"]["results"]
            )
            lines.append(
                f"\n**{_markdown_cell(run['ticker'])} invariant violations:** "
                f"{_markdown_cell(invariant_codes)}"
            )
        if run["errors"]:
            lines.append(
                f"\n**{_markdown_cell(run['ticker'])} errors:** "
                f"{_markdown_cell(json.dumps(run['errors']))}"
            )
    summary = report.get("summary", {})
    latency = summary.get("latency_ms", {})
    cost = summary.get("estimated_cost_usd", {})
    lines.extend(
        [
            "",
            "## Aggregate agentic run",
            "",
            f"- Completed analyses: {summary.get('agentic_run_count', 0)}",
            f"- End-to-end latency p50/p95: {latency.get('p50')} / {latency.get('p95')} ms",
            f"- Total estimated cost: {cost.get('total', 0.0):.6f} USD "
            f"({cost.get('pending_run_count', 0)} pending)",
            f"- Total token usage: {_markdown_cell(json.dumps(summary.get('token_usage', {}), sort_keys=True))}",
        ]
    )
    return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    escaped = html.escape(" ".join(value.splitlines()), quote=True)
    for character in r"\`*_[]()!|:/":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _decision(analysis: dict[str, Any] | None) -> str:
    return analysis.get("decision", "blocked") if analysis is not None else "blocked"


if __name__ == "__main__":
    from app.runtime import run_job

    sys.exit(run_job(main))
