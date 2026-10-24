from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.schemas.analyze import AnalysisTrace
from scripts import trust_e2e


def test_runtime_summary_reports_latency_percentiles_and_cost():
    runs = [
        {
            "status": "passed",
            "trace": {
                "total_latency_ms": 100.0,
                "estimated_cost_usd": 0.001,
            },
        },
        {
            "status": "failed",
            "trace": {
                "total_latency_ms": 300.0,
                "estimated_cost_usd": 0.003,
            },
        },
        {
            "status": "blocked",
            "trace": None,
        },
    ]

    summary = trust_e2e._runtime_summary(runs)

    assert summary == {
        "completed_run_count": 2,
        "latency_ms": {"mean": 200.0, "p50": 100.0, "p95": 300.0},
        "estimated_cost_usd": {"mean": 0.002, "p95": 0.003, "total": 0.004},
    }


def test_runtime_summary_handles_runs_without_completed_traces():
    summary = trust_e2e._runtime_summary([{"status": "blocked", "trace": None}])

    assert summary == {
        "completed_run_count": 0,
        "latency_ms": {"mean": None, "p50": None, "p95": None},
        "estimated_cost_usd": {"mean": None, "p95": None, "total": 0.0},
    }


def test_trace_summary_reports_analysis_date_and_source(monkeypatch):
    monkeypatch.setattr(
        trust_e2e,
        "get_settings",
        lambda: SimpleNamespace(GEMINI_MODEL="test-model"),
    )
    monkeypatch.setattr(trust_e2e, "estimate_usage_cost_usd", lambda model, usage: 0.0)

    summary = trust_e2e._trace_summary(
        AnalysisTrace(
            run_id="historical-run",
            ticker="NVDA",
            as_of_date=date(2024, 1, 2),
            as_of_date_source="request",
        )
    )

    assert summary["as_of_date"] == "2024-01-02"
    assert summary["as_of_date_source"] == "request"
