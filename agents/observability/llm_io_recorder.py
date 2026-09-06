"""Optional, bounded capture of the inputs and events exposed by ADK."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.config import AgentWorkflowConfig
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)
MAX_DEBUG_FILE_BYTES = 1_048_576


class NullLlmIoRecorder:
    """Null object used when debug capture is disabled."""

    enabled = False

    def record_run_config(self, payload: dict[str, Any]) -> None: pass
    def record_agent_prompts(self, registry: Any) -> None: pass
    def record_user_message(self, text: str) -> None: pass
    def record_event(self, index: int, event: Any) -> None: pass
    def record_summary(self, payload: dict[str, Any]) -> None: pass


class FileLlmIoRecorder:
    """Write bounded JSON files without allowing diagnostics to affect analysis."""

    enabled = True

    def __init__(self, run_directory: Path) -> None:
        self.run_directory = run_directory

    def _write(self, filename: str, payload: Any) -> None:
        path = self.run_directory / filename
        try:
            encoded = json.dumps(payload, default=str, indent=2, ensure_ascii=False)
            encoded_bytes = encoded.encode("utf-8")
            if len(encoded_bytes) > MAX_DEBUG_FILE_BYTES:
                encoded = json.dumps(
                    {"truncated": True, "content": encoded_bytes[:MAX_DEBUG_FILE_BYTES].decode("utf-8", "ignore")},
                    indent=2,
                )
            path.write_text(encoded + "\n", encoding="utf-8")
        except OSError as exc:
            # Debugging must never take down an analysis when its directory is unwritable.
            logger.warning("llm_io_capture_failed path=%s reason=%s", path, exc)
        except Exception as exc:
            logger.warning("llm_io_capture_failed path=%s reason=%s", path, exc)

    def record_run_config(self, payload: dict[str, Any]) -> None:
        self._write("000-run-config.json", payload)

    def record_agent_prompts(self, registry: Any) -> None:
        agents = list(registry.specialists.values())
        if registry.root_agent is not None:
            agents.append(registry.root_agent)
        prompts = []
        for agent in agents:
            prompts.append({
                "name": getattr(agent, "name", None),
                "model": getattr(agent, "model", None),
                "temperature": getattr(getattr(agent, "generate_content_config", None), "temperature", None),
                "instruction": getattr(agent, "instruction", None),
                "tool_names": [getattr(tool, "name", str(tool)) for tool in (getattr(agent, "tools", None) or [])],
                "sub_agent_names": [getattr(child, "name", str(child)) for child in (getattr(agent, "sub_agents", None) or [])],
            })
        self._write("001-agent-prompts.json", {"agents": prompts})

    def record_user_message(self, text: str) -> None:
        self._write("002-user-message.json", {"text": text})

    def record_event(self, index: int, event: Any) -> None:
        if hasattr(event, "model_dump"):
            try:
                raw = event.model_dump(mode="json")
            except Exception:
                raw = repr(event)
        else:
            raw = event if isinstance(event, dict) else repr(event)
        self._write(f"003-event-{index:03d}.json", {"raw": raw})

    def record_summary(self, payload: dict[str, Any]) -> None:
        self._write("999-summary.json", payload)


def build_llm_io_recorder(
    run_id: str,
    settings: Settings | None = None,
    config: AgentWorkflowConfig | None = None,
) -> NullLlmIoRecorder | FileLlmIoRecorder:
    settings = settings or get_settings()
    enabled = config.debug_llm_io if config is not None else settings.DEBUG_LLM_IO
    directory = config.debug_llm_io_dir if config is not None else settings.DEBUG_LLM_IO_DIR
    if not enabled:
        return NullLlmIoRecorder()
    run_directory = Path(directory) / run_id
    try:
        run_directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("llm_io_capture_failed path=%s reason=%s", run_directory, exc)
    return FileLlmIoRecorder(run_directory)
