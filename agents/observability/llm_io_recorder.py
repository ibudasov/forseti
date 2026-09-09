"""Optional, bounded capture of the inputs and events exposed by ADK."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agents.config import AgentWorkflowConfig
from agents.orchestration.adk_events import (
    event_author,
    event_error,
    event_function_calls,
    event_function_responses,
    event_text,
    event_token_usage,
)
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)
MAX_DEBUG_FILE_BYTES = 1_048_576


def _value(event: Any, name: str) -> Any:
    if isinstance(event, dict):
        return event.get(name)
    return getattr(event, name, None)


class NullLlmIoRecorder:
    """Null object used when debug capture is disabled."""

    enabled = False
    run_directory: Path | None = None

    def record_run_config(self, payload: dict[str, Any]) -> None:
        return None

    def record_agent_prompts(self, registry: Any) -> None:
        return None

    def record_user_message(self, text: str) -> None:
        return None

    def record_event(self, index: int, event: Any) -> None:
        return None

    def record_summary(self, payload: dict[str, Any]) -> None:
        return None


class FileLlmIoRecorder:
    """Write bounded JSON files without allowing diagnostics to affect analysis."""

    enabled = True

    def __init__(self, run_directory: Path) -> None:
        self.run_directory = run_directory

    @staticmethod
    def _serialize_payload(payload: Any) -> str:
        return json.dumps(payload, default=str, indent=2, ensure_ascii=False)

    def _truncate_payload(self, serialized_payload: str) -> str:
        original_size_bytes = len(serialized_payload.encode("utf-8"))
        low = 0
        high = len(serialized_payload)
        best_content = ""

        while low <= high:
            middle = (low + high) // 2
            candidate = {
                "truncated": True,
                "original_size_bytes": original_size_bytes,
                "content": serialized_payload[:middle],
            }
            encoded_candidate = self._serialize_payload(candidate).encode("utf-8")
            if len(encoded_candidate) <= MAX_DEBUG_FILE_BYTES:
                best_content = serialized_payload[:middle]
                low = middle + 1
                continue
            high = middle - 1

        return self._serialize_payload(
            {
                "truncated": True,
                "original_size_bytes": original_size_bytes,
                "content": best_content,
            }
        )

    @staticmethod
    def _tool_name(tool: Any) -> str:
        tool_name = getattr(tool, "name", None)
        if tool_name:
            return str(tool_name)
        return str(getattr(tool, "__name__", tool))

    @staticmethod
    def _generation_temperature(agent: Any) -> Any:
        generation_config = getattr(agent, "generate_content_config", None) or {}
        if isinstance(generation_config, dict):
            return generation_config.get("temperature")
        return getattr(generation_config, "temperature", None)

    @staticmethod
    def _raw_event(event: Any) -> Any:
        if isinstance(event, dict):
            return event
        if hasattr(event, "model_dump"):
            try:
                return event.model_dump(mode="json")
            except Exception:
                return repr(event)
        return repr(event)

    def _write(self, filename: str, payload: Any) -> None:
        path = self.run_directory / filename
        serialized_payload = self._serialize_payload(payload)
        if len(serialized_payload.encode("utf-8")) > MAX_DEBUG_FILE_BYTES:
            serialized_payload = self._truncate_payload(serialized_payload)
        try:
            path.write_text(serialized_payload, encoding="utf-8")
        except OSError as exc:
            # Debugging must never take down an analysis when its directory is unwritable.
            logger.warning("llm_io_capture_failed path=%s reason=%s", path, exc)

    def record_run_config(self, payload: dict[str, Any]) -> None:
        self._write("000-run-config.json", payload)

    def record_agent_prompts(self, registry: Any) -> None:
        agents = list(registry.specialists.values())
        if registry.root_agent is not None:
            agents.append(registry.root_agent)
        prompts = []
        for agent in agents:
            prompts.append(
                {
                    "name": getattr(agent, "name", None),
                    "model": getattr(agent, "model", None),
                    "temperature": self._generation_temperature(agent),
                    "instruction": getattr(agent, "instruction", None),
                    "tool_names": [
                        self._tool_name(tool) for tool in (getattr(agent, "tools", None) or [])
                    ],
                    "sub_agent_names": [
                        getattr(child, "name", str(child))
                        for child in (getattr(agent, "sub_agents", None) or [])
                    ],
                }
            )
        self._write("001-agent-prompts.json", {"agents": prompts})

    def record_user_message(self, text: str) -> None:
        self._write("002-user-message.json", {"text": text})

    def record_event(self, index: int, event: Any) -> None:
        self._write(
            f"003-event-{index:03d}.json",
            {
                "author": event_author(event),
                "text": event_text(event),
                "function_calls": event_function_calls(event),
                "function_responses": event_function_responses(event),
                "usage_metadata": event_token_usage(event),
                "error": event_error(event),
                "error_code": _value(event, "error_code"),
                "error_message": _value(event, "error_message"),
                "raw": self._raw_event(event),
            },
        )

    def record_summary(self, payload: dict[str, Any]) -> None:
        self._write("999-summary.json", payload)


def build_llm_io_recorder(
    run_id: str,
    settings: Settings | None = None,
    config: AgentWorkflowConfig | None = None,
) -> NullLlmIoRecorder | FileLlmIoRecorder:
    resolved_settings = settings
    if config is None and resolved_settings is None:
        resolved_settings = get_settings()

    assert config is not None or resolved_settings is not None
    enabled = config.debug_llm_io if config is not None else resolved_settings.DEBUG_LLM_IO
    directory = config.debug_llm_io_dir if config is not None else resolved_settings.DEBUG_LLM_IO_DIR
    if not enabled:
        return NullLlmIoRecorder()
    run_directory = Path(directory) / run_id
    try:
        run_directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("llm_io_capture_failed path=%s reason=%s", run_directory, exc)
        return NullLlmIoRecorder()
    return FileLlmIoRecorder(run_directory)
