"""Tests for offline ADK cassette replay."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import agents.orchestration.workflow as workflow_module
from agents.config import load_agent_config
from agents.orchestration.adk_events import event_error, event_function_calls, event_text, event_token_usage
from agents.orchestration.cassette_runner import (
    CassetteExhaustedError,
    CassetteNotFoundError,
    build_cassette_runner_factory,
)
from agents.orchestration.registry import AgentRegistry
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from app.settings import Settings


class _NoopRecorder:
    enabled = False

    def record_run_config(self, payload):
        return None

    def record_agent_prompts(self, registry):
        return None

    def record_user_message(self, text):
        return None

    def record_event(self, index, event):
        return None

    def record_summary(self, payload):
        return None


class _Resolved:
    is_valid = True
    ticker = "NVDA"
    error = None


def _deterministic_response() -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision="watchlist",
        confidence=0.5,
        reasons=["fixture"],
        warnings=[],
        engine_version="v1.rules.0",
        trace_id="deterministic-trace",
    )


def _write_event(directory: Path, index: int, payload: dict) -> None:
    (directory / f"003-event-{index:03d}.json").write_text(json.dumps(payload), encoding="utf-8")


def _workflow(monkeypatch, runner_factory):
    monkeypatch.setattr(workflow_module, "resolve_ticker", lambda ticker_reference: _Resolved())
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _deterministic_response().model_copy(deep=True),
    )
    monkeypatch.setattr(
        workflow_module,
        "build_agent_registry",
        lambda config, engine=None, today=None: AgentRegistry(tools={}, specialists={}, root_agent=None),
    )
    monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_persist_trace", lambda self, trace: None)
    return AgenticAnalysisWorkflow(
        load_agent_config(Settings(_env_file=None)),
        runner_factory=runner_factory,
        llm_io_recorder=_NoopRecorder(),
    )


def test_runner_yields_events_in_filename_order(tmp_path):
    cassette_dir = tmp_path / "cassette"
    cassette_dir.mkdir()
    _write_event(
        cassette_dir,
        1,
        {
            "author": "technical_analyst",
            "text": "trend",
            "function_calls": ["calculate_risk"],
            "function_responses": [{"ok": True}],
            "usage_metadata": {"prompt_token_count": 3, "total_token_count": 5},
            "error_code": "TOOL_ERROR",
            "error_message": "tool missing",
        },
    )
    _write_event(
        cassette_dir,
        0,
        {
            "author": "fundamental_analyst",
            "text": "growth",
            "function_calls": [],
            "function_responses": [],
            "usage_metadata": {"prompt_token_count": 2, "total_token_count": 4},
            "error_code": None,
            "error_message": None,
        },
    )

    events = list(build_cassette_runner_factory(cassette_dir)(None, "NVDA"))

    assert [event.author for event in events] == ["fundamental_analyst", "technical_analyst"]
    assert event_text(events[0]) == "growth"
    assert event_function_calls(events[0]) == []
    assert event_token_usage(events[0]) == {"prompt_token_count": 2, "total_token_count": 4}
    assert event_text(events[1]) == "trend"
    assert event_function_calls(events[1]) == ["calculate_risk"]
    assert event_token_usage(events[1]) == {"prompt_token_count": 3, "total_token_count": 5}
    assert event_error(events[1]) == "tool missing"


def test_missing_directory_raises_cassette_not_found():
    runner = build_cassette_runner_factory(Path("/does/not/exist"))

    with pytest.raises(CassetteNotFoundError, match="Cassette directory not found"):
        list(runner(None, "NVDA"))


def test_missing_event_index_raises_cassette_exhausted(tmp_path):
    cassette_dir = tmp_path / "cassette"
    cassette_dir.mkdir()
    _write_event(cassette_dir, 1, {"author": "fundamental_analyst"})

    with pytest.raises(CassetteExhaustedError, match="003-event-000.json"):
        list(build_cassette_runner_factory(cassette_dir)(None, "NVDA"))


def test_empty_directory_keeps_workflow_outside_agent_layer(monkeypatch, tmp_path):
    cassette_dir = tmp_path / "cassette"
    cassette_dir.mkdir()

    response = _workflow(monkeypatch, build_cassette_runner_factory(cassette_dir)).analyze(
        "NVDA",
        request=AnalyzeRequest(ticker="NVDA", as_of_date=date(2026, 3, 1)),
    )

    assert response.trace is not None
    assert response.trace.entered_agent_layer is False
    assert response.trace.adk_event_count == 0


def test_malformed_event_names_the_file(tmp_path):
    cassette_dir = tmp_path / "cassette"
    cassette_dir.mkdir()
    (cassette_dir / "003-event-000.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="003-event-000.json"):
        list(build_cassette_runner_factory(cassette_dir)(None, "NVDA"))


def test_workflow_accepts_cassette_runner_factory(monkeypatch, tmp_path):
    cassette_dir = tmp_path / "cassette"
    cassette_dir.mkdir()
    _write_event(
        cassette_dir,
        0,
        {
            "author": "decision_synthesizer",
            "text": "{\"decision\":\"watchlist\",\"confidence\":0.5,\"reasons\":[]}",
            "function_calls": [],
            "function_responses": [],
            "usage_metadata": {},
            "error_code": None,
            "error_message": None,
        },
    )

    response = _workflow(monkeypatch, build_cassette_runner_factory(cassette_dir)).analyze(
        "NVDA",
        request=AnalyzeRequest(ticker="NVDA", as_of_date=date(2026, 3, 1)),
    )

    assert response.trace is not None
    assert response.trace.entered_agent_layer is True
    assert response.trace.observed_agents == ["decision_synthesizer"]
