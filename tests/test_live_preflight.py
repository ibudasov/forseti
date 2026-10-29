from __future__ import annotations

from typing import Any

import pytest

from app.schemas.analyze import AnalysisTrace, AnalyzeResponse, DecisionDiagnosis, TraceStep
from scripts import live_preflight


class StubPipeline:
    def __init__(self, response: AnalyzeResponse) -> None:
        self.response = response
        self.requests = []

    def analyze(self, request) -> AnalyzeResponse:
        self.requests.append(request)
        return self.response


def _response(
    *,
    decision: str = "watchlist",
    confidence: float = 0.5,
    warnings: list[str] | None = None,
    trace: AnalysisTrace | None = None,
) -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision=decision,
        confidence=confidence,
        reasons=["fixture"],
        warnings=warnings or [],
        entry_range=None,
        stop_loss=None,
        take_profit=None,
        risk_reward=None,
        position_size_eur=None,
        engine_version="fixture",
        trace_id="fixture-run",
        time_stop_at=None,
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="fixture",
            detail="fixture",
            checklist_score=0,
            debug_reason="fixture",
        ),
        trace=trace,
    )


def _trace(
    *,
    warnings: list[str] | None = None,
    step_status: str = "completed",
    synthesis_output: dict[str, Any] | None = None,
) -> AnalysisTrace:
    return AnalysisTrace(
        run_id="fixture-run",
        ticker="NVDA",
        final_decision="watchlist",
        total_latency_ms=150.0,
        token_usage={"prompt_token_count": 10, "candidates_token_count": 5, "total_token_count": 15},
        warnings=warnings or [],
        steps=[
            TraceStep(sequence=1, agent_name="fundamental_analyst", status="completed", latency_ms=20),
            TraceStep(sequence=2, agent_name="technical_analyst", status="completed", latency_ms=30),
            TraceStep(
                sequence=3,
                agent_name="decision_synthesizer",
                status=step_status,
                latency_ms=100,
                output=synthesis_output,
            ),
            TraceStep(
                sequence=4,
                agent_name="critic_guardrail",
                status="skipped",
                output={"reason": "critic_mode_off"},
            ),
        ],
    )


def test_run_preflight_compares_pipeline_outputs_and_reports_cost_and_invariants(monkeypatch):
    linear = StubPipeline(_response(decision="trade", confidence=0.8))
    agentic = StubPipeline(_response(trace=_trace()))
    monkeypatch.setattr(live_preflight, "estimate_usage_cost_usd", lambda model, usage: 0.002)

    report = live_preflight.run_preflight(
        ["NVDA"],
        linear_pipeline=linear,
        agentic_pipeline=agentic,
        model_name="fixture-model",
        critic_mode="off",
    )

    run = report["runs"][0]
    assert run["status"] == "passed"
    assert run["linear"]["decision"] == "trade"
    assert run["agentic"]["decision"] == "watchlist"
    assert run["agentic"]["stage_latency_ms"]["technical_analyst"] == 30
    assert run["agentic"]["token_usage"]["total_token_count"] == 15
    assert run["agentic"]["estimated_cost_usd"] == 0.002
    assert run["invariants"] == {"passed": True, "results": []}
    assert linear.requests[0].ticker == "NVDA"
    assert report["summary"]["agentic_run_count"] == 1
    assert report["summary"]["latency_ms"]["p50"] == report["summary"]["latency_ms"]["p95"]
    assert report["summary"]["estimated_cost_usd"]["total"] == 0.002


def test_run_preflight_reports_degradation_and_failed_invariants(monkeypatch):
    linear = StubPipeline(_response(decision="watchlist", confidence=0.4))
    agentic = StubPipeline(
        _response(
            decision="trade",
            confidence=0.8,
            trace=_trace(
                warnings=["guardrail_rejected: rejected"],
                step_status="degraded",
                synthesis_output={"reason": "unparsable_synthesis", "used_fallback": True},
            ),
        )
    )
    monkeypatch.setattr(live_preflight, "estimate_usage_cost_usd", lambda model, usage: None)

    report = live_preflight.run_preflight(
        ["NVDA"],
        linear_pipeline=linear,
        agentic_pipeline=agentic,
        model_name="unpriced-model",
        critic_mode="off",
    )

    run = report["runs"][0]
    assert run["status"] == "failed"
    assert run["agentic"]["cost_status"] == "pending"
    assert run["agentic"]["degraded_flags"] == {
        "unparsable_synthesis": True,
        "guardrail_rejected": True,
        "agent_fallbacks": ["decision_synthesizer"],
        "degraded_agents": ["decision_synthesizer"],
    }
    assert {item["code"] for item in run["invariants"]["results"]} == {
        "decision_upgrade",
        "confidence_upgrade",
    }


def test_run_preflight_continues_after_pipeline_exception(monkeypatch):
    class BrokenPipeline:
        def analyze(self, request) -> Any:
            raise RuntimeError("model unavailable")

    linear = StubPipeline(_response())
    report = live_preflight.run_preflight(
        ["NVDA"],
        linear_pipeline=linear,
        agentic_pipeline=BrokenPipeline(),
        model_name="fixture-model",
        critic_mode="off",
    )

    assert report["runs"][0]["status"] == "blocked"
    assert report["runs"][0]["errors"]["agentic"] == "RuntimeError: model unavailable"
    assert report["runs"][0]["invariants"] is None


def test_requested_tickers_defaults_to_first_five_and_rejects_duplicates(tmp_path):
    matrix_path = tmp_path / "tickers.json"
    matrix_path.write_text(
        '{"tickers": [{"ticker": "NVDA"}, {"ticker": "AMD"}, {"ticker": "LMT"}, '
        '{"ticker": "FSLR"}, {"ticker": "IONQ"}, {"ticker": "SERV"}]}',
        encoding="utf-8",
    )

    assert live_preflight._requested_tickers(None, matrix_path) == [
        "NVDA",
        "AMD",
        "LMT",
        "FSLR",
        "IONQ",
    ]
    with pytest.raises(ValueError, match="duplicates"):
        live_preflight._requested_tickers("nvda,NVDA", matrix_path)


def test_parse_args_requires_explicit_cost_confirmation():
    with pytest.raises(SystemExit):
        live_preflight.parse_args([])


def test_aggregate_summary_calculates_latency_percentiles_and_total_tokens():
    summary = live_preflight._aggregate_summary(
        [
            {
                "agentic": {
                    "total_latency_ms": 100.0,
                    "token_usage": {"total_token_count": 10},
                    "estimated_cost_usd": 0.001,
                }
            },
            {
                "agentic": {
                    "total_latency_ms": 200.0,
                    "token_usage": {"total_token_count": 20},
                    "estimated_cost_usd": None,
                }
            },
        ]
    )

    assert summary == {
        "agentic_run_count": 2,
        "latency_ms": {"p50": 150.0, "p95": 195.0},
        "token_usage": {"total_token_count": 30},
        "estimated_cost_usd": {"total": 0.001, "pending_run_count": 1},
    }


def test_markdown_summary_includes_decisions_latency_and_pending_cost():
    report = {
        "generated_at": "2026-10-29T00:00:00+00:00",
        "model_name": "fixture-model",
        "runs": [
            {
                "ticker": "NVDA",
                "status": "passed",
                "linear": {"decision": "trade"},
                "agentic": {
                    "decision": "watchlist",
                    "warnings": [],
                    "degraded_flags": {"agent_fallbacks": []},
                    "stage_latency_ms": {"decision_synthesizer": 12.5},
                    "total_latency_ms": 13.0,
                    "token_usage": {"total_token_count": 20},
                    "estimated_cost_usd": None,
                },
                "invariants": {"passed": True, "results": []},
                "errors": {},
            }
        ],
        "summary": {
            "agentic_run_count": 1,
            "latency_ms": {"p50": 13.0, "p95": 13.0},
            "token_usage": {"total_token_count": 20},
            "estimated_cost_usd": {"total": 0.0, "pending_run_count": 1},
        },
    }

    summary = live_preflight.render_markdown(report)

    assert "| NVDA | passed | trade | watchlist |" in summary
    assert r"decision\_synthesizer: 12.5 ms" in summary
    assert "End-to-end latency p50/p95: 13.0 / 13.0 ms" in summary
    assert "| 20 | pending |" in summary


def test_markdown_cell_escapes_untrusted_warning_markup():
    escaped = live_preflight._markdown_cell("error | [link](https://example.test) <tag>\nsecond line")

    assert r"\|" in escaped
    assert r"\[" in escaped and r"\]" in escaped
    assert "&lt;tag&gt;" in escaped
    assert r"https\:\/\/example.test" in escaped
    assert "\n" not in escaped
