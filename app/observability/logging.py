from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format application records for Cloud Logging's structured payload."""

    EVENT_FIELDS = (
        "event",
        "decision",
        "pipeline",
        "cost_status",
        "degraded",
        "confidence",
        "total_latency_ms",
        "agent_latency_ms",
        "token_usage",
        "cost_usd",
        "pricing_version",
        "degraded_reasons",
        "agent_effects",
        "agent_modes",
        "agent",
        "reason",
        "status",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "run_id": getattr(record, "run_id", None),
            "ticker": getattr(record, "ticker", None),
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
        }
        payload.update(
            {
                field: getattr(record, field)
                for field in self.EVENT_FIELDS
                if hasattr(record, field)
            }
        )
        payload.update(_current_trace_fields())
        return json.dumps(payload, default=str, separators=(",", ":"))


def _current_trace_fields() -> dict[str, str]:
    try:
        from opentelemetry import trace
    except ImportError:
        return {}

    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}

    result = {
        "logging.googleapis.com/spanId": f"{span_context.span_id:016x}",
    }
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if project:
        result["logging.googleapis.com/trace"] = (
            f"projects/{project}/traces/{span_context.trace_id:032x}"
        )
    return result


def configure_logging() -> None:
    """Configure text logs locally and structured JSON logs when requested."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not root_logger.handlers:
        root_logger.addHandler(logging.StreamHandler(sys.stdout))

    formatter: logging.Formatter
    if os.getenv("LOG_FORMAT", "text").lower() == "json":
        formatter = JsonFormatter()
        for handler in root_logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is not sys.stdout:
                handler.setStream(sys.stdout)
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setFormatter(formatter)
