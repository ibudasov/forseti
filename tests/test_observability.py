import json
import logging

from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState

from app.observability import tracing
from app.observability.events import log_analysis_completed
from app.observability.logging import JsonFormatter
from app.schemas.analyze import AnalysisTrace, AnalyzeResponse, TraceStep


def test_json_formatter_includes_structured_fields_and_current_trace(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    record = logging.makeLogRecord(
        {
            "name": "app.api",
            "level": logging.INFO,
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "analysis_completed",
            "args": (),
            "run_id": "run-123",
            "ticker": "NVDA",
        }
    )
    span = NonRecordingSpan(
        SpanContext(
            trace_id=0x123,
            span_id=0x456,
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        )
    )

    with trace.use_span(span):
        payload = json.loads(JsonFormatter().format(record))

    assert payload["severity"] == "INFO"
    assert payload["message"] == "analysis_completed"
    assert payload["logger"] == "app.api"
    assert payload["run_id"] == "run-123"
    assert payload["ticker"] == "NVDA"
    assert payload["logging.googleapis.com/trace"] == (
        "projects/test-project/traces/00000000000000000000000000000123"
    )
    assert payload["logging.googleapis.com/spanId"] == "0000000000000456"


def test_configure_tracing_is_a_noop_without_exporter(monkeypatch):
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.setattr(tracing, "_configured", False)

    assert tracing.configure_tracing() is False
    assert tracing._configured is False


def test_analysis_completed_uses_persisted_trace_cost_and_agent_latency(caplog):
    trace = AnalysisTrace(
        run_id="run-cost",
        trace_id="a" * 32,
        ticker="NVDA",
        total_latency_ms=150,
        token_usage={"total_token_count": 8},
        estimated_cost_usd=0.2500006,
        cost_status="priced",
        pricing_version="catalog-v1",
        steps=[
            TraceStep(
                sequence=1,
                agent_name="technical_analyst",
                status="completed",
                latency_ms=50,
            ),
            TraceStep(
                sequence=2,
                agent_name="technical_analyst",
                status="completed",
                latency_ms=25,
            ),
        ],
    )
    response = AnalyzeResponse(
        ticker="NVDA",
        decision="watchlist",
        confidence=0.7,
        reasons=[],
        warnings=[],
        engine_version="test",
        trace_id="run-cost",
        trace=trace,
    )

    with caplog.at_level(logging.INFO, logger="app.observability.events"):
        log_analysis_completed(
            response,
            pipeline="agentic",
            agent_modes={"fundamental": "enforced", "technical": "enforced", "critic": "off"},
        )

    completed = next(record for record in caplog.records if record.event == "analysis_completed")
    assert completed.cost_usd == 0.250001
    assert completed.cost_status == "priced"
    assert completed.pricing_version == "catalog-v1"
    assert completed.total_latency_ms == 150
    assert completed.agent_latency_ms == {"technical_analyst": 75}
    assert completed.token_usage == {"total_token_count": 8}
