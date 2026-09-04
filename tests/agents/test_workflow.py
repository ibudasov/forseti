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
