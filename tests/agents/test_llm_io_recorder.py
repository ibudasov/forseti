from __future__ import annotations

import json

from agents.observability.llm_io_recorder import (
    FileLlmIoRecorder,
    NullLlmIoRecorder,
    build_llm_io_recorder,
)
from app.settings import Settings


def test_disabled_capture_is_a_null_recorder(tmp_path):
    recorder = build_llm_io_recorder(
        "run-1", Settings(_env_file=None, DEBUG_LLM_IO_DIR=str(tmp_path))
    )
    assert isinstance(recorder, NullLlmIoRecorder)
    assert list(tmp_path.iterdir()) == []


def test_file_recorder_writes_ordered_run_files(tmp_path):
    recorder = FileLlmIoRecorder(tmp_path / "run-1")
    recorder.run_directory.mkdir()
    recorder.record_run_config({"ticker": "NVDA"})
    recorder.record_user_message("Analyze ticker NVDA")
    recorder.record_event(0, {"author": "critic_guardrail"})
    recorder.record_summary({"event_count": 1})

    assert [path.name for path in sorted(recorder.run_directory.iterdir())] == [
        "000-run-config.json",
        "002-user-message.json",
        "003-event-000.json",
        "999-summary.json",
    ]
    assert json.loads((recorder.run_directory / "003-event-000.json").read_text())["raw"]["author"] == "critic_guardrail"
