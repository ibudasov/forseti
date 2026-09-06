"""Tests for ADK workflow trace metadata."""
from __future__ import annotations

import pytest

from agents.config import load_agent_config
from agents.orchestration.workflow import (
    AgenticAnalysisWorkflow,
    GoogleWorkflowError,
    _token_usage_from_event,
)
from app.schemas.analyze import TraceStep
from app.settings import Settings


def _workflow(runner_factory):
    return AgenticAnalysisWorkflow(load_agent_config(Settings(_env_file=None)), runner_factory=runner_factory)


class _Response:
    decision = "no_trade"


def _steps():
    return [TraceStep(sequence=i + 1, agent_name=f"agent_{i}", status="completed") for i in range(7)]


def test_model_error_event_degrades_to_warning_instead_of_failing():
    class _ErrorEvent:
        error_code = "NOT_FOUND"
        error_message = "Tool 'analyze_fundamentals' not found.\nAvailable tools: transfer_to_agent"

    warnings: list[str] = []
    workflow = _workflow(lambda registry, ticker: [_ErrorEvent()])

    workflow._run_adk(None, "NVDA", "run-1", _steps(), _Response(), warnings)

    assert warnings == ["agent_narration_degraded: Tool 'analyze_fundamentals' not found."]


def test_runner_exception_still_raises_google_workflow_error():
    def _boom(registry, ticker):
        raise RuntimeError("vertex unavailable")

    with pytest.raises(GoogleWorkflowError):
        _workflow(_boom)._run_adk(None, "NVDA", "run-1", _steps(), _Response(), [])


def test_token_usage_reads_adk_usage_metadata_object():
    class Usage:
        prompt_token_count = 10
        candidates_token_count = 4
        total_token_count = 14

    class Event:
        usage_metadata = Usage()

    assert _token_usage_from_event(Event()) == {
        "prompt_token_count": 10,
        "candidates_token_count": 4,
        "total_token_count": 14,
    }


def test_token_usage_reads_mapping_metadata():
    event = {"usage_metadata": {"prompt_token_count": 3, "total_token_count": 3}}

    assert _token_usage_from_event(event) == {
        "prompt_token_count": 3,
        "total_token_count": 3,
    }


def test_runner_events_are_the_only_trace_steps():
    class Event:
        author = "technical_analyst"

    steps: list[TraceStep] = []
    workflow = _workflow(lambda registry, ticker: [Event()])

    workflow._run_adk(None, "NVDA", "run-1", steps, _Response(), [])

    assert [(step.agent_name, step.status) for step in steps] == [
        ("technical_analyst", "completed")
    ]


def test_failed_runner_event_is_recorded_with_reason():
    class Event:
        author = "decision_synthesizer"
        error_code = "TOOL_NOT_FOUND"
        error_message = "calculate_risk was not available"

    steps: list[TraceStep] = []
    warnings: list[str] = []
    workflow = _workflow(lambda registry, ticker: [Event()])

    workflow._run_adk(None, "NVDA", "run-1", steps, _Response(), warnings)

    assert steps[0].status == "failed"
    assert steps[0].output == {
        "error_code": "TOOL_NOT_FOUND",
        "error_message": "calculate_risk was not available",
    }
    assert warnings == ["agent_narration_degraded: calculate_risk was not available"]


def test_mapping_runner_error_is_recorded_with_failed_status():
    steps: list[TraceStep] = []
    workflow = _workflow(lambda registry, ticker: [{
        "author": "risk_manager",
        "error_code": "TIMEOUT",
        "error_message": "risk tool timed out",
    }])

    workflow._run_adk(None, "NVDA", "run-1", steps, _Response(), [])

    assert steps[0].status == "failed"
    assert steps[0].agent_name == "risk_manager"
