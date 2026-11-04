from __future__ import annotations

import os
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

_configured = False
_provider: Any = None
_instrumented_apps: set[int] = set()


def configure_tracing(app: FastAPI | None = None) -> bool:
    """Install Cloud Trace instrumentation only when explicitly enabled."""
    global _configured, _provider

    if os.getenv("OTEL_TRACES_EXPORTER", "").lower() != "gcp":
        return False

    if not _configured:
        from opentelemetry import propagate, trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.propagators.cloud_trace_propagator import CloudTraceFormatPropagator
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        provider = TracerProvider(
            resource=Resource.create(
                {"service.name": os.getenv("OTEL_SERVICE_NAME", "forseti-api")}
            )
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                CloudTraceSpanExporter(project_id=os.getenv("GOOGLE_CLOUD_PROJECT"))
            )
        )
        _provider = provider
        trace.set_tracer_provider(provider)
        propagate.set_global_textmap(
            CompositePropagator(
                [
                    CloudTraceFormatPropagator(),
                    TraceContextTextMapPropagator(),
                ]
            )
        )
        RequestsInstrumentor().instrument()
        SQLAlchemyInstrumentor().instrument()
        _configured = True

    if app is not None and id(app) not in _instrumented_apps:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        _instrumented_apps.add(id(app))
    return True


def shutdown_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()


def analysis_span():
    if not _configured:
        return nullcontext()
    from opentelemetry import trace

    return trace.get_tracer("forseti").start_as_current_span("forseti.analysis")


def set_analysis_span_attributes(
    *,
    response: Any,
    pipeline: str,
    fundamental_mode: str,
    technical_mode: str,
    critic_mode: str,
) -> None:
    """Attach the final analysis outcome to the active request span."""
    try:
        from opentelemetry import trace
    except ImportError:
        return

    span = trace.get_current_span()
    if not span.is_recording():
        return

    run_id = response.trace.run_id if response.trace is not None else response.trace_id
    span.set_attribute("forseti.run_id", run_id or "")
    span.set_attribute("ticker", response.ticker)
    span.set_attribute("forseti.pipeline", pipeline)
    span.set_attribute("forseti.agent_mode.fundamental", fundamental_mode)
    span.set_attribute("forseti.agent_mode.technical", technical_mode)
    span.set_attribute("forseti.agent_mode.critic", critic_mode)
    span.set_attribute("forseti.final_decision", response.decision)
    degraded_reasons = list(response.warnings)
    if response.trace is not None:
        degraded_reasons.extend(
            f"{step.agent_name}:{step.output.get('reason', step.status)}"
            for step in response.trace.steps
            if step.status == "degraded" and step.output is not None
        )
    degraded_reasons = list(dict.fromkeys(degraded_reasons))
    span.set_attribute("forseti.degraded", bool(degraded_reasons))
    if degraded_reasons:
        span.set_attribute("forseti.degraded_reasons", degraded_reasons)


def current_trace_id() -> str | None:
    try:
        from opentelemetry import trace
    except ImportError:
        return None

    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return f"{span_context.trace_id:032x}"
