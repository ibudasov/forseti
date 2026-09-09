from __future__ import annotations

import pytest

from agents.orchestration.trace_recorder import MAX_TRACE_OUTPUT_CHARS, TraceRecorder


def test_steps_are_numbered_in_insertion_order():
    recorder = TraceRecorder()

    recorder.record_completed("input_resolver")
    recorder.record_degraded("technical_analyst", "agent_narration_degraded")
    recorder.record_skipped("critic_guardrail", "critic_never_reached")

    steps = recorder.steps()
    assert [step.sequence for step in steps] == [1, 2, 3]


def test_record_skipped_requires_a_reason():
    recorder = TraceRecorder()

    with pytest.raises(ValueError, match="reason must not be empty"):
        recorder.record_skipped("retriever", "")


def test_output_strings_are_truncated():
    recorder = TraceRecorder()

    recorder.record_completed("fundamental_analyst", output={"text": "x" * (MAX_TRACE_OUTPUT_CHARS + 20)})

    output_text = recorder.steps()[0].output["text"]
    assert output_text.startswith("x" * MAX_TRACE_OUTPUT_CHARS)
    assert output_text.endswith("…[truncated]")


def test_observed_agents_excludes_skipped_and_keeps_first_seen_order():
    recorder = TraceRecorder()

    recorder.record_skipped("retriever", "retriever_tool_unavailable")
    recorder.record_completed("trade_analyst_supervisor")
    recorder.record_degraded("trade_analyst_supervisor", "agent_narration_degraded")
    recorder.record_completed("fundamental_analyst")

    assert recorder.observed_agents() == ["trade_analyst_supervisor", "fundamental_analyst"]
