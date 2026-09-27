"""Tests for observed ADK workflow trace metadata."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import agents.orchestration.workflow as workflow_module
from agents.config import load_agent_config
from agents.observability.llm_io_recorder import FileLlmIoRecorder, NullLlmIoRecorder
from agents.orchestration.registry import AgentRegistry, build_agent_registry as build_real_agent_registry
from agents.orchestration.workflow import AgenticAnalysisWorkflow, GoogleWorkflowError, load_trace
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse, DecisionDiagnosis
from app.schemas.critique import (
    CritiqueObjection,
    CritiqueRequest,
    CritiqueResponse,
    CritiqueResult,
    CritiqueValidation,
    DraftRecommendation,
)
from app.schemas.fundamentals import (
    CitedFinding,
    EvidenceChunkInput,
    EvidenceQuestionCoverage,
    FundamentalAnalysisRequest,
    FundamentalAssessmentResponse,
    FundamentalAssessmentResult,
    FundamentalAssessmentValidation,
    FundamentalContextCoverage,
)
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


def _watchlist_response() -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision="watchlist",
        confidence=0.64,
        reasons=["deterministic"],
        warnings=[],
        engine_version="v1.rules.0",
        trace_id="deterministic-trace",
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="score_below_trade",
            detail="score 7/11, missing: none",
            checklist_score=7,
            debug_reason="checklist/score_below_trade: score 7/11, missing: none",
        ),
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
    monkeypatch.setattr(
        workflow_module,
        "build_critique_request",
        lambda *args, **kwargs: _critique_request(kwargs["draft"]),
    )
    monkeypatch.setattr(workflow_module, "evaluate_deterministic_objections", lambda **kwargs: [])
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
    _enable_critic(monkeypatch, mode="shadow")
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


def test_finalizer_ordering_applies_fundamental_then_synthesizer_then_critic(monkeypatch):
    monkeypatch.setattr(workflow_module, "_fundamental_agent_mode", lambda: "enforced")
    _enable_critic(
        monkeypatch,
        mode="enforced",
        critique_result=_critique_result(
            verdict="reject",
            proposed_decision="no_trade",
            objections=[_model_objection()],
        ),
    )
    workflow = _workflow(
        monkeypatch,
        events=[
            _event("decision_synthesizer", text='{"decision":"watchlist","confidence":0.5,"reasons":["downgrade"]}'),
            _event("critic_guardrail", text="critic narration"),
        ],
    )
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )
    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", lambda *a, **k: _request())
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: _AnalystStub(_assessment(accepted=True, adjustment=1)),
    )
    monkeypatch.setattr(
        workflow_module,
        "get_latest_bars",
        lambda *a, **k: [SimpleNamespace(high=110, low=90, close=100)] * 100,
    )
    monkeypatch.setattr(
        workflow_module,
        "calculate_risk_levels",
        lambda bars, risk_config: SimpleNamespace(
            entry_low=98,
            entry_high=102,
            stop_loss=90,
            take_profit_1=110,
            take_profit_2=120,
            risk_reward=2,
            position_size_eur=500,
        ),
    )
    monkeypatch.setattr(
        workflow_module,
        "calculate_time_stop_at",
        lambda recommendation_date: date(2026, 12, 31),
    )

    response = workflow.analyze("NVDA")

    assert response.fundamental_agent_effect is not None
    assert response.fundamental_agent_effect.final_decision == "trade"
    assert response.decision == "no_trade"
    assert response.critic_effect is not None
    assert response.critic_effect.final_decision == "no_trade"
    assert all("ignored_due_to_fundamental_policy" not in str(step.output) for step in response.trace.steps)
    critic_chain = [
        step.agent_name
        for step in response.trace.steps
        if step.agent_name in {"decision_synthesizer", "critic_guardrail"}
    ]
    assert critic_chain == [
        "decision_synthesizer",
        "critic_guardrail",
    ]


def test_revision_loop_invokes_synthesizer_once_more(monkeypatch):
    revision_calls: list[str] = []
    _enable_critic(
        monkeypatch,
        mode="enforced",
        critique_result=_critique_result(
            verdict="revise",
            revision_instructions="Lower confidence.",
            objections=[_model_objection()],
        ),
    )

    def _fake_revision(self, **kwargs):
        revision_calls.append(kwargs["revision_instructions"])
        return workflow_module._DecisionRevisionResult(
            synthesis=workflow_module.DecisionSynthesis(
                decision="no_trade",
                confidence=0.2,
                reasons=["revised"],
            ),
            raw_output='{"decision":"no_trade","confidence":0.2,"reasons":["revised"]}',
            token_usage={"total_token_count": 4},
            latency_ms=5.0,
        )

    monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_request_synthesis_revision", _fake_revision)
    workflow = _workflow(
        monkeypatch,
        events=[
            _event("decision_synthesizer", text='{"decision":"watchlist","confidence":0.5,"reasons":["first"]}'),
            _event("critic_guardrail", text="revise"),
        ],
    )
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )
    response = workflow.analyze("NVDA")

    assert revision_calls == ["Lower confidence."]
    assert response.decision == "no_trade"
    assert response.critic_effect is not None
    assert response.critic_effect.revisions_performed == 1
    synth_step = [step for step in response.trace.steps if step.agent_name == "decision_synthesizer"][0]
    assert synth_step.retries == 1


def test_revision_loop_cannot_run_twice(monkeypatch):
    revision_calls: list[str] = []
    _enable_critic(
        monkeypatch,
        mode="enforced",
        critique_result=_critique_result(
            verdict="revise",
            revision_instructions="Only once.",
            objections=[_model_objection()],
        ),
    )

    def _fake_revision(self, **kwargs):
        revision_calls.append(kwargs["revision_instructions"])
        return workflow_module._DecisionRevisionResult(
            synthesis=workflow_module.DecisionSynthesis(
                decision="watchlist",
                confidence=0.3,
                reasons=["revised"],
            ),
            raw_output='{"decision":"watchlist","confidence":0.3,"reasons":["revised"]}',
            token_usage={},
            latency_ms=1.0,
        )

    monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_request_synthesis_revision", _fake_revision)
    _workflow(
        monkeypatch,
        events=[
            _event("decision_synthesizer", text='{"decision":"watchlist","confidence":0.5,"reasons":["first"]}'),
            _event("critic_guardrail", text="revise"),
        ],
    ).analyze("NVDA")

    assert revision_calls == ["Only once."]


def test_failed_revision_keeps_first_draft_and_marks_degraded(monkeypatch):
    _enable_critic(
        monkeypatch,
        mode="enforced",
        critique_result=_critique_result(
            verdict="revise",
            revision_instructions="Try again.",
            objections=[_model_objection()],
        ),
    )
    monkeypatch.setattr(
        workflow_module.AgenticAnalysisWorkflow,
        "_request_synthesis_revision",
        lambda self, **kwargs: workflow_module._DecisionRevisionResult(
            synthesis=None,
            raw_output="bad output",
            token_usage={},
            latency_ms=3.0,
            error_reason="critic_revision_failed",
            error_detail="timeout",
        ),
    )
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )

    workflow = _workflow(
        monkeypatch,
        events=[
            _event("decision_synthesizer", text='{"decision":"watchlist","confidence":0.5,"reasons":["first"]}'),
            _event("critic_guardrail", text="revise"),
        ],
    )
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )
    response = workflow.analyze("NVDA")

    assert response.decision == "watchlist"
    assert "critic_revision_failed" in response.warnings
    synth_step = [step for step in response.trace.steps if step.agent_name == "decision_synthesizer"][0]
    assert synth_step.status == "degraded"
    critic_step = [step for step in response.trace.steps if step.agent_name == "critic_guardrail"][0]
    assert critic_step.status == "degraded"


def test_critic_model_failure_preserves_http_success_and_deterministic_answer(monkeypatch):
    _enable_critic(
        monkeypatch,
        mode="enforced",
        critique_result=_critique_result(
            accepted=False,
            status="failed",
            reason_codes=["provider_error:timeout"],
        ),
    )

    response = _workflow(
        monkeypatch,
        events=[
            _event("decision_synthesizer", text='{"decision":"no_trade","confidence":0.0,"reasons":["first"]}'),
            _event("critic_guardrail", text="failure"),
        ],
    ).analyze("NVDA")

    assert response.decision == "no_trade"
    critic_step = [step for step in response.trace.steps if step.agent_name == "critic_guardrail"][0]
    assert critic_step.status == "degraded"


def test_critic_mode_off_skips_policy_and_keeps_current_response(monkeypatch):
    monkeypatch.setattr(workflow_module, "_critic_mode", lambda: "off")
    monkeypatch.setattr(
        workflow_module.AgenticAnalysisWorkflow,
        "_review_critique",
        lambda self, critique_request: (_ for _ in ()).throw(AssertionError("critic should not run")),
    )
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )

    workflow = _workflow(
        monkeypatch,
        events=[
            _event("decision_synthesizer", text='{"decision":"watchlist","confidence":0.5,"reasons":["first"]}'),
            _event("critic_guardrail", text="skip"),
        ],
    )
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )
    response = workflow.analyze("NVDA")

    assert response.decision == "watchlist"
    assert response.critic_effect is None
    critic_step = [step for step in response.trace.steps if step.agent_name == "critic_guardrail"][0]
    assert critic_step.status == "skipped"
    assert critic_step.output == {"reason": "critic_mode_off"}


def test_critic_shadow_mode_populates_effect_without_mutating_fields(monkeypatch):
    _enable_critic(
        monkeypatch,
        mode="shadow",
        critique_result=_critique_result(
            verdict="reject",
            proposed_decision="no_trade",
            penalty=0.2,
            objections=[_model_objection()],
        ),
    )
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )

    workflow = _workflow(
        monkeypatch,
        events=[
            _event("decision_synthesizer", text='{"decision":"watchlist","confidence":0.5,"reasons":["first"]}'),
            _event("critic_guardrail", text="shadow"),
        ],
    )
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )
    response = workflow.analyze("NVDA")

    assert response.decision == "watchlist"
    assert response.confidence == 0.5
    assert response.critic_effect is not None
    assert response.critic_effect.final_decision == "no_trade"
    assert response.entry_range == _watchlist_response().entry_range
    assert response.stop_loss == _watchlist_response().stop_loss
    assert response.take_profit == _watchlist_response().take_profit
    assert response.position_size_eur == _watchlist_response().position_size_eur


def test_money_fields_remain_identical_across_critic_modes(monkeypatch):
    for mode in ("off", "shadow", "enforced"):
        _enable_critic(
            monkeypatch,
            mode=mode,
            critique_result=_critique_result(
                verdict="reject",
                proposed_decision="no_trade",
                objections=[_model_objection()],
            ),
        )
        response = _workflow(
            monkeypatch,
            events=[
                _event("decision_synthesizer", text='{"decision":"watchlist","confidence":0.5,"reasons":["first"]}'),
                _event("critic_guardrail", text=mode),
            ],
        ).analyze("NVDA")
        baseline = _deterministic_response()
        assert response.entry_range == baseline.entry_range
        assert response.stop_loss == baseline.stop_loss
        assert response.take_profit == baseline.take_profit
        assert response.risk_reward == baseline.risk_reward
        assert response.position_size_eur == baseline.position_size_eur


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


def test_fundamental_mode_off_skips_context_builder(monkeypatch):
    monkeypatch.setattr(workflow_module, "_fundamental_agent_mode", lambda: "off")
    monkeypatch.setattr(
        workflow_module,
        "build_fundamental_analysis_request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not build context")),
    )
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: (_ for _ in ()).throw(AssertionError("should not call analyst")),
    )

    response = _workflow(monkeypatch, events=()).analyze("NVDA")

    assert response.fundamental_agent_effect is None


def test_fundamental_shadow_mode_records_counterfactual_without_changing_baseline(monkeypatch):
    monkeypatch.setattr(workflow_module, "_fundamental_agent_mode", lambda: "shadow")
    workflow = _workflow(monkeypatch, events=())
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )
    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", lambda *a, **k: _request())
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: _AnalystStub(_assessment(accepted=True, adjustment=1)),
    )

    response = workflow.analyze("NVDA")

    assert response.decision == "watchlist"
    assert response.fundamental_agent_effect is not None
    assert response.fundamental_agent_effect.mode == "shadow"
    assert response.fundamental_agent_effect.counterfactual_decision == "trade"
    assert response.fundamental_agent_effect.final_decision == "watchlist"
    assert response.trace.fundamental_agent_effect is not None


def test_fundamental_enforced_mode_promotes_trade_with_deterministic_risk(monkeypatch):
    monkeypatch.setattr(workflow_module, "_fundamental_agent_mode", lambda: "enforced")
    workflow = _workflow(monkeypatch, events=())
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: _watchlist_response().model_copy(deep=True),
    )
    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", lambda *a, **k: _request())
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: _AnalystStub(_assessment(accepted=True, adjustment=1)),
    )
    monkeypatch.setattr(
        workflow_module,
        "get_latest_bars",
        lambda *a, **k: [SimpleNamespace(high=110, low=90, close=100)] * 100,
    )
    monkeypatch.setattr(
        workflow_module,
        "calculate_risk_levels",
        lambda bars, risk_config: SimpleNamespace(
            entry_low=98,
            entry_high=102,
            stop_loss=90,
            take_profit_1=110,
            take_profit_2=120,
            risk_reward=2,
            position_size_eur=500,
        ),
    )
    monkeypatch.setattr(
        workflow_module,
        "calculate_time_stop_at",
        lambda recommendation_date: date(2026, 12, 31),
    )

    response = workflow.analyze("NVDA")

    assert response.decision == "trade"
    assert response.entry_range == (98.0, 102.0)
    assert response.stop_loss == 90.0
    assert response.position_size_eur == 500.0
    assert response.fundamental_agent_effect is not None
    assert response.fundamental_agent_effect.final_decision == "trade"


class _AnalystStub:
    def __init__(self, result):
        self._result = result

    def assess(self, request):
        return self._result


def _request() -> FundamentalAnalysisRequest:
    return FundamentalAnalysisRequest(
        run_id="run-1",
        context_hash="ctx-1",
        ticker="NVDA",
        company_name="NVIDIA",
        sector="ai",
        currency="USD",
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        as_of_date=date(2026, 9, 8),
        deterministic_result=_fundamental_result(),
        metric_series=[],
        evidence_chunks=[
            EvidenceChunkInput(
                chunk_id=10,
                retrieval_question_id="growth_sustainability",
                source_type="filing_business",
                source_url="https://example.com/10",
                source_hash="hash-10",
                chunk_index=0,
                quality_tier="primary",
                text="Recurring demand supports growth.",
            )
        ],
        coverage=FundamentalContextCoverage(
            required_metrics_present=["revenue_growth"],
            required_metrics_missing=[],
            annual_period_counts={},
            quarterly_period_counts={},
            source_types_present=["filing_business"],
            source_types_missing=[],
            evidence_stale=False,
            evidence_truncated=False,
            metric_series_truncated=False,
            selected_chunk_count=1,
            selected_character_count=32,
            question_coverage=[
                EvidenceQuestionCoverage(
                    question_id="growth_sustainability",
                    chunk_ids=[10],
                    source_types=["filing_business"],
                    truncated=False,
                )
            ],
        ),
    )


def _fundamental_result():
    from decimal import Decimal

    from app.domain.fundamentals import DeterministicFundamentalAnalysis, FundamentalMetricRef, FundamentalRuleResult

    return DeterministicFundamentalAnalysis(
        ticker="NVDA",
        as_of_date=date(2026, 6, 30),
        score=2,
        maximum_score=6,
        rule_results=[
            FundamentalRuleResult(
                rule_id="revenue_growth",
                status="passed",
                points_awarded=2,
                points_available=2,
                metric_ids=["revenue_growth:2026-06-30"],
                explanation="revenue_growth: 0.20 > 0.15 min",
            )
        ],
        warnings=[],
        metrics=[
            FundamentalMetricRef(
                metric_id="revenue_growth:2026-06-30",
                name="Revenue growth",
                value=Decimal("0.20"),
                unit="ratio",
                period_end=date(2026, 6, 30),
            )
        ],
    )


def _assessment(*, accepted: bool, adjustment: int) -> FundamentalAssessmentResult:
    response = FundamentalAssessmentResponse(
        run_id="run-1",
        context_hash="ctx-1",
        status="completed",
        overall_signal="positive",
        proposed_score_adjustment=adjustment,
        findings=[
            CitedFinding(
                finding_id="f1",
                category="growth_quality",
                direction="positive",
                materiality="high",
                claim="Supported claim.",
                metric_ids=["revenue_growth:2026-06-30"],
                chunk_ids=[10],
            )
        ],
        contradictions=[],
        material_red_flags=[],
        evidence_coverage=0.5,
        missing_information=[],
        summary="Assessment summary.",
    )
    return FundamentalAssessmentResult(
        response=response,
        validation=FundamentalAssessmentValidation(accepted=accepted, reason_codes=[]),
        raw_output="",
        latency_ms=0.0,
        token_usage={},
        model_name="fake",
        prompt_version="v1",
    )


def _enable_critic(
    monkeypatch,
    *,
    mode: str,
    critique_result: CritiqueResult | None = None,
):
    monkeypatch.setattr(workflow_module, "_critic_mode", lambda: mode)
    monkeypatch.setattr(
        workflow_module.AgenticAnalysisWorkflow,
        "_review_critique",
        lambda self, critique_request: critique_result or _critique_result(),
    )


def _critique_result(
    *,
    accepted: bool = True,
    status: str = "completed",
    verdict: str = "accept",
    proposed_decision: str | None = None,
    penalty: float = 0.0,
    reason_codes: list[str] | None = None,
    objections: list[CritiqueObjection] | None = None,
    revision_instructions: str = "",
) -> CritiqueResult:
    response = CritiqueResponse.model_construct(
        schema_version="1.0",
        run_id="run-1",
        context_hash="ctx-1",
        agent_name="critic_guardrail",
        status=status,
        verdict=verdict,
        objections=objections or [],
        proposed_decision=proposed_decision,
        proposed_confidence_penalty=penalty,
        revision_instructions=revision_instructions,
        summary="Critic summary.",
    )
    return CritiqueResult(
        response=response,
        validation=CritiqueValidation(accepted=accepted, reason_codes=reason_codes or []),
        raw_output="{}",
        latency_ms=6.0,
        token_usage={"total_token_count": 3},
        model_name="fake-critic",
        prompt_version="critic-guardrail.v1",
    )


def _model_objection() -> CritiqueObjection:
    return CritiqueObjection.model_construct(
        objection_id="model:1",
        category="unsupported_claim",
        severity="high",
        source="model",
        claim="Unsupported claim.",
        metric_ids=["revenue_growth:2026-06-30"],
        chunk_ids=[10],
        warning_codes=["warn:stale_data"],
    )


def _critique_request(draft: DraftRecommendation) -> CritiqueRequest:
    return CritiqueRequest(
        schema_version="1.0",
        run_id="run-1",
        context_hash="ctx-1",
        ticker="NVDA",
        as_of_date=date(2026, 9, 27),
        snapshot_at=datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
        draft=draft,
        analyst_views=[],
        evidence_chunks=[],
        deterministic_warnings=[],
        allowed_actions=[
            "accept",
            "downgrade_confidence",
            "downgrade_decision",
            "force_no_trade",
            "request_revision",
        ],
    )
