"""On-demand cloud trust check: the live pre-flight against Cloud SQL, compared with the local baseline."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from scripts import live_preflight

CONFIRM_COST_VARIABLE = "CONFIRM_COST"
REPORT_DIRECTORY_VARIABLE = "TRUST_REPORT_DIR"
BASELINE_REPORT_VARIABLE = "TRUST_BASELINE_REPORT"
DEFAULT_REPORT_DIRECTORY = "reports/trust"
NOT_AVAILABLE = "n/a"


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    """Reduce a pre-flight report to the go-live trust measurements."""
    runs = report["runs"]
    agentic = [run["agentic"] for run in runs if run["agentic"] is not None]
    analysis_count = len(agentic)
    downgrades: dict[str, int] = {}
    for analysis in agentic:
        for agent in analysis.get("downgraded_agents", []):
            downgrades[agent] = downgrades.get(agent, 0) + 1
    summary = report["summary"]
    return {
        "analysis_count": analysis_count,
        "invariant_violations": sum(len(_violations(run)) for run in runs),
        "blocked_runs": sum(1 for run in runs if run["errors"]),
        "unparsable_synthesis": sum(
            1 for analysis in agentic if analysis["degraded_flags"]["unparsable_synthesis"]
        ),
        "fallback_rate": _rate(analysis_count, [a for a in agentic if a["degraded_flags"]["agent_fallbacks"]]),
        "degraded_rate": _rate(analysis_count, [a for a in agentic if a["degraded_flags"]["degraded_agents"]]),
        "downgrade_rate_per_agent": {
            agent: count / analysis_count for agent, count in sorted(downgrades.items())
        },
        "p95_latency_ms": summary["latency_ms"]["p95"],
        "cost_per_analysis_usd": _cost_per_analysis(summary, analysis_count),
        "unpriced_runs": summary["estimated_cost_usd"]["pending_run_count"],
    }


def hard_failures(summary: dict[str, Any]) -> list[str]:
    """Trust invariants that must hold on cloud data; any entry blocks go-live."""
    failures = []
    if summary["analysis_count"] == 0:
        failures.append("no agentic analysis completed")
    if summary["blocked_runs"]:
        failures.append(f"{summary['blocked_runs']} run(s) errored")
    if summary["invariant_violations"]:
        failures.append(f"{summary['invariant_violations']} invariant violation(s)")
    if summary["unparsable_synthesis"]:
        failures.append(f"{summary['unparsable_synthesis']} unparsable_synthesis run(s)")
    return failures


def render_comparison(cloud: dict[str, Any], baseline: dict[str, Any] | None) -> str:
    lines = ["| Measure | Cloud | Local baseline |", "|---|---|---|"]
    for label, key, formatter in _MEASURES:
        baseline_value = formatter(baseline[key]) if baseline is not None else NOT_AVAILABLE
        lines.append(f"| {label} | {formatter(cloud[key])} | {baseline_value} |")
    agents = sorted(set(cloud["downgrade_rate_per_agent"]) | set((baseline or {}).get("downgrade_rate_per_agent", {})))
    for agent in agents:
        cloud_rate = cloud["downgrade_rate_per_agent"].get(agent, 0.0)
        baseline_rate = (baseline or {}).get("downgrade_rate_per_agent", {}).get(agent, 0.0)
        baseline_cell = _percent(baseline_rate) if baseline is not None else NOT_AVAILABLE
        lines.append(f"| Downgrade rate: {agent} | {_percent(cloud_rate)} | {baseline_cell} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    if os.environ.get(CONFIRM_COST_VARIABLE) != "yes":
        print(f"Error: the trust check spends model budget; set {CONFIRM_COST_VARIABLE}=yes.")
        return 2
    args = _parse_args(argv)
    preflight_args = live_preflight.parse_args(
        ["--confirm-cost", "yes", "--report-dir", args.report_dir, *(["--tickers", args.tickers] if args.tickers else [])]
    )
    report, json_path = live_preflight.execute_preflight(preflight_args)
    cloud = summarize(report)
    baseline = summarize(json.loads(Path(args.baseline).read_text(encoding="utf-8"))) if args.baseline else None
    comparison = render_comparison(cloud, baseline)
    json_path.with_suffix(".trust.md").write_text(comparison, encoding="utf-8")
    print(comparison)
    failures = hard_failures(cloud)
    for failure in failures:
        print(f"Trust check failed: {failure}")
    return 1 if failures else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the cloud trust check (requires CONFIRM_COST=yes).")
    parser.add_argument("--tickers", default=None)
    parser.add_argument("--report-dir", default=os.environ.get(REPORT_DIRECTORY_VARIABLE, DEFAULT_REPORT_DIRECTORY))
    parser.add_argument("--baseline", default=os.environ.get(BASELINE_REPORT_VARIABLE))
    return parser.parse_args(argv)


def _violations(run: dict[str, Any]) -> list[Any]:
    invariants = run["invariants"]
    return [] if invariants is None else invariants["results"]


def _rate(analysis_count: int, matching: list[Any]) -> float:
    return len(matching) / analysis_count if analysis_count else 0.0


def _cost_per_analysis(summary: dict[str, Any], analysis_count: int) -> float | None:
    if not analysis_count or summary["estimated_cost_usd"]["pending_run_count"]:
        return None
    return summary["estimated_cost_usd"]["total"] / analysis_count


def _percent(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value:.0%}"


def _milliseconds(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value:.0f} ms"


def _dollars(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"${value:.4f}"


_MEASURES = (
    ("Invariant violations (must be 0)", "invariant_violations", str),
    ("unparsable_synthesis (must be 0)", "unparsable_synthesis", str),
    ("Fallback rate", "fallback_rate", _percent),
    ("Degraded rate", "degraded_rate", _percent),
    ("p95 latency", "p95_latency_ms", _milliseconds),
    ("Cost per analysis", "cost_per_analysis_usd", _dollars),
)


if __name__ == "__main__":
    from app.runtime import run_job

    sys.exit(run_job(main))
