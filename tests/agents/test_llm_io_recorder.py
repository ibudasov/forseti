from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from agents.config import HARD_RULES_TEXT, load_agent_config
from agents.observability.llm_io_recorder import (
    MAX_DEBUG_FILE_BYTES,
    FileLlmIoRecorder,
    NullLlmIoRecorder,
    build_llm_io_recorder,
)
from agents.orchestration.registry import build_agent_registry
from app.settings import Settings


class _BrokenDumpEvent:
    def __init__(self) -> None:
        self.author = "critic_guardrail"
        self.content = SimpleNamespace(parts=[SimpleNamespace(text="fallback")])
        self.usage_metadata = None
        self.error_code = None
        self.error_message = None

    def model_dump(self, mode: str = "json") -> dict[str, str]:
        raise RuntimeError("cannot serialize")


def _object_event() -> SimpleNamespace:
    return SimpleNamespace(
        author="fundamental_analyst",
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(text="bullish"),
                SimpleNamespace(function_call=SimpleNamespace(name="transfer_to_agent")),
                SimpleNamespace(function_response=SimpleNamespace(result="ok")),
            ]
        ),
        usage_metadata=SimpleNamespace(
            prompt_token_count=7,
            candidates_token_count=3,
            total_token_count=10,
        ),
        error_code=None,
        error_message=None,
    )


def _dict_event() -> dict[str, object]:
    return {
        "author": "technical_analyst",
        "content": {
            "parts": [
                {"text": "constructive"},
                {"function_call": {"name": "calculate_risk"}},
                {"function_response": {"name": "calculate_risk", "response": {"ok": True}}},
            ]
        },
        "usage_metadata": {"total_token_count": 11},
        "error_code": "TOOL_ERROR",
        "error_message": "tool missing\nextra line",
    }


def test_disabled_capture_is_a_null_recorder(tmp_path):
    recorder = build_llm_io_recorder(
        "run-1",
        settings=Settings(_env_file=None, DEBUG_LLM_IO_DIR=str(tmp_path)),
    )

    assert isinstance(recorder, NullLlmIoRecorder)
    assert list(tmp_path.iterdir()) == []


def test_enabled_capture_writes_expected_files_in_order(tmp_path):
    recorder = build_llm_io_recorder(
        "run-1",
        settings=Settings(
            _env_file=None,
            DEBUG_LLM_IO=True,
            DEBUG_LLM_IO_DIR=str(tmp_path),
        ),
    )
    registry = build_agent_registry(load_agent_config(Settings(_env_file=None)))

    recorder.record_run_config({"ticker": "NVDA", "pipeline_mode": "agentic"})
    recorder.record_agent_prompts(registry)
    recorder.record_user_message("Analyze ticker NVDA")
    recorder.record_event(0, _object_event())
    recorder.record_summary({"event_count": 1, "final_decision": "no_trade"})

    run_directory = tmp_path / "run-1"
    assert [path.name for path in sorted(run_directory.iterdir())] == [
        "000-run-config.json",
        "001-agent-prompts.json",
        "002-user-message.json",
        "003-event-000.json",
        "999-summary.json",
    ]

    prompts_payload = json.loads((run_directory / "001-agent-prompts.json").read_text())
    prompts_by_name = {agent["name"]: agent for agent in prompts_payload["agents"]}
    assert HARD_RULES_TEXT in prompts_by_name["fundamental_analyst"]["instruction"]
    assert HARD_RULES_TEXT in prompts_by_name["technical_analyst"]["instruction"]
    assert HARD_RULES_TEXT in prompts_by_name["decision_synthesizer"]["instruction"]
    assert HARD_RULES_TEXT in prompts_by_name["critic_guardrail"]["instruction"]


def test_event_serialization_handles_object_dict_broken_and_empty_events(tmp_path):
    recorder = FileLlmIoRecorder(tmp_path / "run-1")
    recorder.run_directory.mkdir()

    recorder.record_event(0, _object_event())
    recorder.record_event(1, _dict_event())
    recorder.record_event(2, _BrokenDumpEvent())
    recorder.record_event(3, {"author": "trade_analyst_supervisor"})

    object_payload = json.loads((recorder.run_directory / "003-event-000.json").read_text())
    assert object_payload["author"] == "fundamental_analyst"
    assert object_payload["text"] == "bullish"
    assert object_payload["function_calls"] == ["transfer_to_agent"]
    assert object_payload["function_responses"] == ["namespace(result='ok')"]
    assert object_payload["usage_metadata"]["total_token_count"] == 10

    dict_payload = json.loads((recorder.run_directory / "003-event-001.json").read_text())
    assert dict_payload["author"] == "technical_analyst"
    assert dict_payload["text"] == "constructive"
    assert dict_payload["function_calls"] == ["calculate_risk"]
    assert dict_payload["function_responses"] == [{"name": "calculate_risk", "response": {"ok": True}}]
    assert dict_payload["error"] == "tool missing"
    assert dict_payload["raw"]["author"] == "technical_analyst"

    broken_payload = json.loads((recorder.run_directory / "003-event-002.json").read_text())
    assert broken_payload["raw"].startswith("<")
    assert broken_payload["text"] == "fallback"

    empty_payload = json.loads((recorder.run_directory / "003-event-003.json").read_text())
    assert empty_payload["text"] == ""
    assert empty_payload["function_calls"] == []
    assert empty_payload["function_responses"] == []


def test_large_payload_is_truncated_and_marked(tmp_path):
    recorder = FileLlmIoRecorder(tmp_path / "run-1")
    recorder.run_directory.mkdir()

    recorder.record_summary({"payload": "x" * (MAX_DEBUG_FILE_BYTES + 1024)})

    summary_path = recorder.run_directory / "999-summary.json"
    summary_payload = json.loads(summary_path.read_text())
    assert summary_payload["truncated"] is True
    assert summary_payload["original_size_bytes"] > MAX_DEBUG_FILE_BYTES
    assert summary_path.stat().st_size <= MAX_DEBUG_FILE_BYTES


def test_unwritable_directory_logs_and_does_not_raise(tmp_path, caplog):
    unwritable_root = tmp_path / "file-not-a-dir"
    unwritable_root.write_text("content", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        recorder = build_llm_io_recorder(
            "run-1",
            settings=Settings(
                _env_file=None,
                DEBUG_LLM_IO=True,
                DEBUG_LLM_IO_DIR=str(unwritable_root),
            ),
        )

    assert isinstance(recorder, NullLlmIoRecorder)
    assert "llm_io_capture_failed" in caplog.text
