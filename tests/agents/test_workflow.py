"""Tests for observed ADK workflow trace metadata."""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

import agents.orchestration.workflow as workflow_module
from agents.config import load_agent_config
from agents.observability.llm_io_recorder import FileLlmIoRecorder, NullLlmIoRecorder
from agents.orchestration.registry import AgentRegistry, build_agent_registry as build_real_agent_registry
from agents.orchestration.workflow import AgenticAnalysisWorkflow, GoogleWorkflowError, load_trace
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from app.settings import Settings


class _NoopRecorder:
    enabled = False

    def record_run_config(self, config):
        return None

    def record_agent_prompts(self, registry):
        return None

    def record_user_message(self, message):
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
        decision="no_trade",
        confidence=0.0,
        reasons=["deterministic"],
        warnings=[],
        engine_version="v1.rules.0",
        trace_id="deterministic-trace",
        entry_range=(100.0, 101.0),
        stop_loss=95.0,
        take_profit=(110.0, 115.0),
        risk_reward=2.0,
        position_size_eur=500.0,
    )


def _event(
    author: str,
    *,
    tool_calls: tuple[str, ...] = (),
    text: str = "",
    token_usage: dict[str, int] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
):
    parts = []
    if text:
        parts.append(SimpleNamespace(text=text))
    parts.extend(SimpleNamespace(function_call=SimpleNamespace(name=name)) for name in tool_calls)
    return SimpleNamespace(
        author=author,
        content=SimpleNamespace(parts=parts),
        usage_metadata=SimpleNamespace(**(token_usage or {})),
        error_code=error_code,
        error_message=error_message,
    )


def _workflow(monkeypatch, *, events=(), raise_error: Exception | None = None, with_retriever: bool = False, engine=None):
    monkeypatch.setattr(workflow_module, "resolve_ticker", lambda ticker_reference: _Resolved())
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _deterministic_response().model_copy(deep=True),
    )
    tools = {"retriever": object()} if with_retriever else {}
    monkeypatch.setattr(
        workflow_module,
        "build_agent_registry",
        lambda config, engine=None, today=None: AgentRegistry(tools=tools, specialists={}, root_agent=None),
    )
    if engine is None:
        monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_persist_trace", lambda self, trace: None)

    def _runner(registry, ticker):
        if raise_error is not None:
            raise raise_error
        return iter(events)

    return AgenticAnalysisWorkflow(
        load_agent_config(Settings(_env_file=None)),
        engine=engine,
        runner_factory=_runner,
        llm_io_recorder=_NoopRecorder(),
    )


def test_happy_trajectory_records_observed_steps(monkeypatch):
    events = [
        _event("trade_analyst_supervisor", tool_calls=("structured_data_collector", "transfer_to_agent")),
        _event("fundamental_analyst", text="bullish"),
        _event("technical_analyst", text="constructive"),
        _event(
            "decision_synthesizer",
            tool_calls=("calculate_risk",),
            text='{"decision":"no_trade","confidence":0.0,"reasons":[]}',
        ),
        _event("critic_guardrail", text="clear"),
    ]
    response = _workflow(monkeypatch, events=events).analyze("NVDA")

    assert [(step.agent_name, step.status) for step in response.trace.steps] == [
        ("input_resolver", "completed"),
        ("deterministic_pipeline", "completed"),
        ("retriever", "skipped"),
        ("trade_analyst_supervisor", "completed"),
        ("fundamental_analyst", "completed"),
        ("technical_analyst", "completed"),
        ("decision_synthesizer", "completed"),
        ("critic_guardrail", "completed"),
    ]
    assert response.trace.entered_agent_layer is True
    assert response.trace.adk_event_count == 5
    assert response.trace.observed_agents == [
        "trade_analyst_supervisor",
        "fundamental_analyst",
        "technical_analyst",
        "decision_synthesizer",
        "critic_guardrail",
    ]


def test_zero_events_marks_specialists_as_skipped(monkeypatch):
    response = _workflow(monkeypatch, events=()).analyze("NVDA")

    assert response.trace.entered_agent_layer is False
    assert response.trace.adk_event_count == 0
    skipped_reasons = {
        step.agent_name: step.output["reason"]
        for step in response.trace.steps
        if step.status == "skipped"
    }
    assert skipped_reasons == {
        "retriever": "retriever_tool_unavailable",
        "fundamental_analyst": "fundamental_analyst_never_reached",
        "technical_analyst": "technical_analyst_never_reached",
        "decision_synthesizer": "decision_synthesizer_never_reached",
        "critic_guardrail": "critic_never_reached",
    }
    assert response.decision == _deterministic_response().decision


def test_missing_critic_is_recorded_as_skipped(monkeypatch):
    events = [
        _event("trade_analyst_supervisor"),
        _event("decision_synthesizer", text='{"decision":"no_trade","confidence":0.0,"reasons":[]}'),
    ]
    response = _workflow(monkeypatch, events=events).analyze("NVDA")

    critic_steps = [step for step in response.trace.steps if step.agent_name == "critic_guardrail"]
    assert len(critic_steps) == 1
    assert critic_steps[0].status == "skipped"
    assert critic_steps[0].output == {"reason": "critic_never_reached"}


def test_error_event_is_degraded_and_warned(monkeypatch):
    events = [
        _event(
            "technical_analyst",
            error_code="TOOL_ERROR",
            error_message="Tool 'analyze_fundamentals' not found.\nAvailable tools: transfer_to_agent",
        )
    ]
    response = _workflow(monkeypatch, events=events).analyze("NVDA")

    assert response.warnings[-1] == "agent_narration_degraded: Tool 'analyze_fundamentals' not found."
    degraded_step = [step for step in response.trace.steps if step.agent_name == "technical_analyst"][0]
    assert degraded_step.status == "degraded"
    assert degraded_step.output["reason"] == "agent_narration_degraded"


def test_runner_exception_persists_failed_partial_trace(monkeypatch, db_engine):
    run_id = "run-explode"
    monkeypatch.setattr(workflow_module, "uuid4", lambda: run_id)

    workflow = _workflow(monkeypatch, raise_error=RuntimeError("vertex unavailable"), engine=db_engine)
    with pytest.raises(GoogleWorkflowError, match="vertex unavailable"):
        workflow.analyze("NVDA")

    trace = load_trace(run_id, engine=db_engine)
    assert trace is not None
    failed_steps = [step for step in trace.steps if step.status == "failed"]
    assert len(failed_steps) == 1
    assert failed_steps[0].output["reason"] == "adk_runner_exception"


def test_guardrail_rejects_fabricated_risk_values(monkeypatch):
    events = [
        _event(
            "decision_synthesizer",
            text=(
                '{"decision":"trade","confidence":0.9,"reasons":["upgrade"],'
                '"stop_loss":1.0}'
            ),
        )
    ]
    deterministic = _deterministic_response()
    response = _workflow(monkeypatch, events=events).analyze("NVDA")

    assert response.decision == deterministic.decision
    assert response.entry_range == deterministic.entry_range
    assert response.stop_loss == deterministic.stop_loss
    assert response.take_profit == deterministic.take_profit
    assert response.position_size_eur == deterministic.position_size_eur

    synthesis_step = [step for step in response.trace.steps if step.agent_name == "decision_synthesizer"][0]
    assert synthesis_step.status == "degraded"
    assert synthesis_step.output["reason"] == "guardrail_rejected"
    assert any(warning.startswith("guardrail_rejected: agent_output_changed_risk_value") for warning in response.warnings)


def test_completed_non_deterministic_steps_do_not_exceed_event_count(monkeypatch):
    events = [_event("fundamental_analyst"), _event("technical_analyst")]
    response = _workflow(monkeypatch, events=events).analyze("NVDA")

    deterministic_names = {"input_resolver", "deterministic_pipeline"}
    completed_non_deterministic = [
        step
        for step in response.trace.steps
        if step.status == "completed" and step.agent_name not in deterministic_names
    ]
    assert len(completed_non_deterministic) <= len(events)


def test_explicit_file_recorder_writes_run_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow_module, "resolve_ticker", lambda ticker_reference: _Resolved())
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _deterministic_response().model_copy(deep=True),
    )
    registry = build_real_agent_registry(load_agent_config(Settings(_env_file=None)))
    monkeypatch.setattr(
        workflow_module,
        "build_agent_registry",
        lambda config, engine=None, today=None: registry,
    )
    monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_persist_trace", lambda self, trace: None)
    recorder = FileLlmIoRecorder(tmp_path / "run-1")
    recorder.run_directory.mkdir()
    events = [_event("trade_analyst_supervisor"), _event("fundamental_analyst", text="bullish")]

    workflow = AgenticAnalysisWorkflow(
        load_agent_config(Settings(_env_file=None)),
        runner_factory=lambda configured_registry, ticker: iter(events),
        llm_io_recorder=recorder,
    )

    workflow.analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=date(2026, 3, 1)))

    assert [path.name for path in sorted(recorder.run_directory.iterdir())] == [
        "000-run-config.json",
        "001-agent-prompts.json",
        "002-user-message.json",
        "003-event-000.json",
        "003-event-001.json",
        "999-summary.json",
    ]
    run_config = json.loads((recorder.run_directory / "000-run-config.json").read_text(encoding="utf-8"))
    summary = json.loads((recorder.run_directory / "999-summary.json").read_text(encoding="utf-8"))
    assert run_config["today"] == "2026-03-01"
    assert summary["deterministic_fields"]["decision"] == "no_trade"


def test_default_workflow_uses_null_recorder_when_capture_disabled(monkeypatch):
    recorder_calls: list[str] = []

    def fake_builder(run_id, config=None):
        recorder_calls.append(run_id)
        return NullLlmIoRecorder()

    monkeypatch.setattr(workflow_module, "build_llm_io_recorder", fake_builder)

    workflow = _workflow(monkeypatch, events=[_event("fundamental_analyst")])
    workflow.llm_io_recorder = None
    response = workflow.analyze("NVDA")

    assert recorder_calls == [response.trace.run_id]
    assert response.trace.adk_event_count == 1
