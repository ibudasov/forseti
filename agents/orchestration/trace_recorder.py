"""Trace recorder that appends observed workflow steps."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from app.schemas.analyze import TraceStep

STEP_STATUSES = ("completed", "degraded", "failed", "skipped")
MAX_TRACE_OUTPUT_CHARS = 2000
_TRUNCATED_SUFFIX = "…[truncated]"


class TraceRecorder:
    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = []

    def record_completed(
        self,
        agent_name: str,
        *,
        tool_calls: Iterable[str] = (),
        output: dict[str, Any] | None = None,
        latency_ms: float = 0.0,
        token_usage: dict[str, int] | None = None,
        retries: int = 0,
    ) -> None:
        self._append_step(
            agent_name,
            status="completed",
            tool_calls=tool_calls,
            output=output,
            latency_ms=latency_ms,
            token_usage=token_usage,
            retries=retries,
        )

    def record_degraded(
        self,
        agent_name: str,
        reason: str,
        *,
        detail: str | None = None,
        tool_calls: Iterable[str] = (),
        output: dict[str, Any] | None = None,
        latency_ms: float = 0.0,
        token_usage: dict[str, int] | None = None,
        retries: int = 0,
    ) -> None:
        self._append_with_reason(
            agent_name,
            status="degraded",
            reason=reason,
            detail=detail,
            tool_calls=tool_calls,
            output=output,
            latency_ms=latency_ms,
            token_usage=token_usage,
            retries=retries,
        )

    def record_failed(
        self,
        agent_name: str,
        reason: str,
        *,
        detail: str | None = None,
        tool_calls: Iterable[str] = (),
        output: dict[str, Any] | None = None,
        latency_ms: float = 0.0,
        token_usage: dict[str, int] | None = None,
        retries: int = 0,
    ) -> None:
        self._append_with_reason(
            agent_name,
            status="failed",
            reason=reason,
            detail=detail,
            tool_calls=tool_calls,
            output=output,
            latency_ms=latency_ms,
            token_usage=token_usage,
            retries=retries,
        )

    def record_skipped(self, agent_name: str, reason: str) -> None:
        self._append_with_reason(
            agent_name,
            status="skipped",
            reason=reason,
            detail=None,
            tool_calls=(),
            output=None,
            latency_ms=0.0,
            token_usage=None,
            retries=0,
        )

    def steps(self) -> list[TraceStep]:
        return [
            TraceStep(sequence=index, **deepcopy(payload))
            for index, payload in enumerate(self._steps, start=1)
        ]

    def observed_agents(self) -> list[str]:
        observed: list[str] = []
        for step in self._steps:
            if step["status"] == "skipped":
                continue
            agent_name = step["agent_name"]
            if agent_name not in observed:
                observed.append(agent_name)
        return observed

    def _append_with_reason(
        self,
        agent_name: str,
        *,
        status: str,
        reason: str,
        detail: str | None,
        tool_calls: Iterable[str],
        output: dict[str, Any] | None,
        latency_ms: float,
        token_usage: dict[str, int] | None,
        retries: int,
    ) -> None:
        if not reason.strip():
            raise ValueError("reason must not be empty")
        merged_output = dict(output or {})
        merged_output["reason"] = reason
        if detail is not None:
            merged_output["detail"] = detail
        self._append_step(
            agent_name,
            status=status,
            tool_calls=tool_calls,
            output=merged_output,
            latency_ms=latency_ms,
            token_usage=token_usage,
            retries=retries,
        )

    def _append_step(
        self,
        agent_name: str,
        *,
        status: str,
        tool_calls: Iterable[str],
        output: dict[str, Any] | None,
        latency_ms: float,
        token_usage: dict[str, int] | None,
        retries: int,
    ) -> None:
        if status not in STEP_STATUSES:
            raise ValueError(f"unsupported status: {status}")
        self._steps.append(
            {
                "agent_name": agent_name,
                "status": status,
                "tool_calls": list(tool_calls),
                "latency_ms": latency_ms,
                "token_usage": dict(token_usage or {}),
                "retries": retries,
                "output": _truncate_output(output),
            }
        )


def _truncate_output(output: dict[str, Any] | None) -> dict[str, Any] | None:
    if output is None:
        return None
    return _truncate_value(output)


def _truncate_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= MAX_TRACE_OUTPUT_CHARS:
            return value
        return value[:MAX_TRACE_OUTPUT_CHARS] + _TRUNCATED_SUFFIX
    if isinstance(value, dict):
        return {key: _truncate_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_value(item) for item in value]
    return value
