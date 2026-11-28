"""Deterministic offline tests for parallel Fundamental & Technical specialist execution (#214)."""
from __future__ import annotations

import json
import socket
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlmodel import Session

import agents.orchestration.workflow as workflow_module
import app.db.session as db_session_module
import app.services.fundamental_context as fundamental_context_module
from agents.config import load_agent_config
from agents.orchestration.registry import SPECIALIST_ORDER
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.db.models import (
    DocumentChunk,
    Fundamental,
    FundamentalObservation,
    MacroDaily,
    PriceBar,
    Security,
    SourceQualityTier,
    SourceType,
    TechnicalFeature,
)
from app.db.repository import (
    upsert_fundamental,
    upsert_fundamental_observations,
    upsert_price_bars,
    upsert_technical_feature,
)
from app.domain.fundamentals import (
    DeterministicFundamentalAnalysis,
    FundamentalMetricRef,
    FundamentalRuleResult,
)
from app.domain.technicals import (
    DeterministicTechnicalAnalysis,
    TechnicalMetricRef,
    TechnicalRuleResult,
)
from app.rag.embedding import MockEmbeddingClient
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse, DecisionDiagnosis
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
from app.schemas.technicals import (
    TechnicalAnalysisRequest,
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
    TechnicalAssessmentValidation,
    TechnicalContextCoverage,
)
from app.services.decision_synthesizer import DecisionSynthesizer, ModelOutput as SynthesizerModelOutput
from app.services.fundamental_analyst import FundamentalAnalyst, RetryableModelError
from app.services.fundamental_context import build_fundamental_analysis_request
from app.services.technical_analyst import TechnicalAnalyst
from app.services.technical_context import build_technical_analysis_request
from app.settings import Settings
from app.trust.invariants import (
    CRITIC_OFF_ALLOWED_SKIPS,
    check_critic_step_zero_redundant_tokens,
    check_downgrade_only_behavior,
    check_fundamental_agent_authority,
    check_specialist_sequence,
    check_technical_agent_effect_rank,
    evaluate_agentic_invariants,
)


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


class _ScriptedSynthesizerModel:
    model_name = "scripted-synthesizer"

    def __init__(self, output: dict, token_usage: dict[str, int] | None = None) -> None:
        self._output = output
        self._token_usage = token_usage or {"total_token_count": 5}

    def generate(self, prompt: str) -> SynthesizerModelOutput:
        request = json.loads(prompt.split("DecisionSynthesisRequest:\n", maxsplit=1)[1])
        memo = {
            "schema_version": "1.0",
            "run_id": request["run_id"],
            "context_hash": request["context_hash"],
            **self._output,
        }
        return SynthesizerModelOutput(text=json.dumps(memo), token_usage=self._token_usage)


def _poison_network(monkeypatch) -> None:
    def _reject_network(*_args, **_kwargs):
        raise AssertionError("Live network calls are forbidden in offline parallel specialist tests.")

    monkeypatch.setattr(socket.socket, "connect", _reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _reject_network)


def _trade_baseline(checklist_score: int = 8) -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision="trade",
        confidence=0.78,
        reasons=["deterministic"],
        warnings=[],
        engine_version="v1.rules.0",
        trace_id="deterministic-trace",
        entry_range=(100.0, 101.0),
        stop_loss=95.0,
        take_profit=(110.0, 115.0),
        risk_reward=2.0,
        position_size_eur=500.0,
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="checklist_passed",
            detail=f"score {checklist_score}/11",
            checklist_score=checklist_score,
            debug_reason=f"checklist/checklist_passed: score {checklist_score}/11",
        ),
    )


def _fundamental_request(run_id: str = "run-1", as_of_date: date = date(2026, 9, 8)) -> FundamentalAnalysisRequest:
    return FundamentalAnalysisRequest(
        run_id=run_id,
        context_hash="fund-ctx-hash-1",
        ticker="NVDA",
        company_name="NVIDIA",
        sector="ai",
        currency="USD",
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        as_of_date=as_of_date,
        deterministic_result=DeterministicFundamentalAnalysis(
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
        ),
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
                text="Demand remains strong.",
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
            selected_character_count=20,
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


def _fundamental_assessment(
    *,
    accepted: bool = True,
    adjustment: int = -2,
    status: str = "completed",
    reason_codes: list[str] | None = None,
) -> FundamentalAssessmentResult:
    response = FundamentalAssessmentResponse(
        run_id="run-1",
        context_hash="fund-ctx-hash-1",
        status=status,
        overall_signal="negative" if adjustment < 0 else "neutral",
        proposed_score_adjustment=adjustment,
        findings=[
            CitedFinding(
                finding_id="f1",
                category="growth_quality",
                direction="negative" if adjustment < 0 else "mixed",
                materiality="high",
                claim="Growth slowing.",
                metric_ids=["revenue_growth:2026-06-30"],
                chunk_ids=[10],
            )
        ]
        if accepted
        else [],
        contradictions=[],
        material_red_flags=[],
        evidence_coverage=0.5,
        missing_information=[],
        summary="Fundamental assessment summary.",
    )
    return FundamentalAssessmentResult(
        response=response,
        validation=FundamentalAssessmentValidation(accepted=accepted, reason_codes=reason_codes or []),
        raw_output="{}",
        latency_ms=4.0,
        token_usage={"prompt_token_count": 3, "total_token_count": 5},
        model_name="fake-fundamental",
        prompt_version="fundamental-analyst.v1",
    )


def _technical_request(
    *,
    run_id: str = "run-1",
    as_of_date: date = date(2026, 9, 8),
    bar_count: int = 200,
    bars_stale: bool = False,
) -> TechnicalAnalysisRequest:
    return TechnicalAnalysisRequest(
        run_id=run_id,
        context_hash="tech-ctx-hash-1",
        ticker="NVDA",
        sector="ai",
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        as_of_date=as_of_date,
        deterministic_result=DeterministicTechnicalAnalysis(
            ticker="NVDA",
            as_of_date=as_of_date,
            score=1,
            maximum_score=5,
            rule_results=[
                TechnicalRuleResult(
                    rule_id="rsi_healthy",
                    status="passed",
                    points_awarded=1,
                    points_available=1,
                    metric_ids=["rsi_14:2026-09-08"],
                    explanation="rsi_14 55.00 healthy",
                ),
            ],
            warnings=[],
            metrics=[
                TechnicalMetricRef(
                    metric_id="rsi_14:2026-09-08",
                    metric_name="rsi_14",
                    value=Decimal("55.00"),
                    unit="rsi_points",
                    as_of_date=as_of_date,
                ),
            ],
        ),
        facts=[],
        series=[],
        coverage=TechnicalContextCoverage(
            bar_count=bar_count,
            required_facts_missing=[],
            latest_bar_date=as_of_date,
            bars_stale=bars_stale,
            series_truncated=False,
        ),
    )


def _technical_assessment(
    *,
    accepted: bool = True,
    adjustment: int = -2,
    status: str = "completed",
    reason_codes: list[str] | None = None,
) -> TechnicalAssessmentResult:
    response = TechnicalAssessmentResponse(
        run_id="run-1",
        context_hash="tech-ctx-hash-1",
        status=status,
        overall_signal="negative" if adjustment < 0 else "neutral",
        trend_state="downtrend" if adjustment < 0 else "range",
        momentum_state="weakening" if adjustment < 0 else "stable",
        proposed_score_adjustment=adjustment,
        findings=[],
        contradictions=[],
        risk_flags=[],
        missing_information=[],
        summary="Technical assessment summary.",
    )
    return TechnicalAssessmentResult(
        response=response,
        validation=TechnicalAssessmentValidation(accepted=accepted, reason_codes=reason_codes or []),
        raw_output="{}",
        latency_ms=4.0,
        token_usage={"prompt_token_count": 2, "total_token_count": 4},
        model_name="fake-technical",
        prompt_version="technical-analyst.v1",
    )


def _configure_settings(
    monkeypatch,
    *,
    fundamental_mode: str,
    technical_mode: str,
    synthesizer_contract: str = "typed",
    critic_mode: str = "off",
) -> Settings:
    settings = Settings(
        _env_file=None,
        FUNDAMENTAL_AGENT_MODE=fundamental_mode,
        TECHNICAL_AGENT_MODE=technical_mode,
        SYNTHESIZER_CONTRACT=synthesizer_contract,
        CRITIC_MODE=critic_mode,
    )
    monkeypatch.setattr(workflow_module, "get_settings", lambda: settings)
    return settings


def _build_offline_workflow(
    monkeypatch,
    *,
    settings: Settings,
    baseline: AnalyzeResponse,
    engine=None,
    memo_decision: str = "no_trade",
    memo_confidence: float = 0.4,
) -> AgenticAnalysisWorkflow:
    _poison_network(monkeypatch)
    monkeypatch.setattr(workflow_module, "resolve_ticker", lambda ticker_reference: _Resolved())
    if engine is None:
        monkeypatch.setattr(
            workflow_module,
            "analyze_request",
            lambda request, engine=None: baseline.model_copy(deep=True),
        )
        monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_persist_trace", lambda self, trace: None)
    synthesizer = DecisionSynthesizer(
        _ScriptedSynthesizerModel(
            {
                "proposed_decision": memo_decision,
                "confidence": memo_confidence,
                "thesis": "Offline synthesis.",
                "key_reasons": [],
                "key_risks": [],
                "what_would_change": [],
            }
        ),
        max_retries=1,
    )
    return AgenticAnalysisWorkflow(
        load_agent_config(settings),
        engine=engine,
        decision_synthesizer=synthesizer,
        llm_io_recorder=_NoopRecorder(),
    )


def _event(author: str, text: str = "") -> SimpleNamespace:
    parts = [SimpleNamespace(text=text)] if text else []
    return SimpleNamespace(
        author=author,
        content=SimpleNamespace(parts=parts),
        usage_metadata=SimpleNamespace(),
        error_code=None,
        error_message=None,
    )


def test_parallel_specialists_execute_concurrently_with_barrier(monkeypatch):
    settings = _configure_settings(monkeypatch, fundamental_mode="enforced", technical_mode="enforced")
    baseline = _trade_baseline()
    workflow = _build_offline_workflow(monkeypatch, settings=settings, baseline=baseline)
    workflow.runner_factory = lambda registry, ticker: iter(
        [
            _event("fundamental_analyst", "ok"),
            _event("technical_analyst", "ok"),
            _event("decision_synthesizer", "typed"),
        ]
    )
    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", lambda *a, **k: _fundamental_request())
    monkeypatch.setattr(workflow_module, "build_technical_analysis_request", lambda *a, **k: _technical_request())

    barrier = threading.Barrier(2, timeout=2.0)
    thread_ids: set[int] = set()
    lock = threading.Lock()
    start_times: list[float] = []
    end_times: list[float] = []

    class _BarrierFundamentalAnalyst:
        def assess(self, request):
            del request
            with lock:
                thread_ids.add(threading.get_ident())
                start_times.append(time.monotonic())
            barrier.wait()
            time.sleep(0.08)
            with lock:
                end_times.append(time.monotonic())
            return _fundamental_assessment(adjustment=-1)

    class _BarrierTechnicalAnalyst:
        def assess(self, request):
            del request
            with lock:
                thread_ids.add(threading.get_ident())
                start_times.append(time.monotonic())
            barrier.wait()
            time.sleep(0.08)
            with lock:
                end_times.append(time.monotonic())
            return _technical_assessment(adjustment=-1)

    monkeypatch.setattr(workflow_module, "FundamentalAnalyst", _BarrierFundamentalAnalyst)
    monkeypatch.setattr(workflow_module, "TechnicalAnalyst", _BarrierTechnicalAnalyst)

    response = workflow.analyze("NVDA")
    specialist_elapsed = max(end_times) - min(start_times)

    assert len(thread_ids) == 2
    assert specialist_elapsed < 0.14
    assert response.fundamental_agent_effect is not None
    assert response.technical_agent_effect is not None


@pytest.mark.parametrize(
    ("fundamental_mode", "technical_mode"),
    [
        ("enforced", "off"),
        ("off", "enforced"),
    ],
)
def test_single_enabled_specialist_runs_directly_without_thread_pool(
    monkeypatch,
    fundamental_mode,
    technical_mode,
):
    settings = _configure_settings(monkeypatch, fundamental_mode=fundamental_mode, technical_mode=technical_mode)
    baseline = _trade_baseline()
    workflow = _build_offline_workflow(monkeypatch, settings=settings, baseline=baseline, memo_decision="watchlist")
    workflow.runner_factory = lambda registry, ticker: iter(
        [
            _event("fundamental_analyst", "ok"),
            _event("technical_analyst", "ok"),
            _event("decision_synthesizer", "typed"),
            _event("critic_guardrail", ""),
        ]
    )
    caller_thread_id = threading.get_ident()
    observed_threads: list[int] = []

    def _forbid_pool(*_args, **_kwargs):
        raise AssertionError("ThreadPoolExecutor must not be instantiated when <= 1 specialist is enabled.")

    monkeypatch.setattr(workflow_module, "ThreadPoolExecutor", _forbid_pool)

    if fundamental_mode == "off":
        monkeypatch.setattr(
            workflow_module,
            "build_fundamental_analysis_request",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("fundamental builder must not run")),
        )
        monkeypatch.setattr(
            workflow_module,
            "FundamentalAnalyst",
            lambda: (_ for _ in ()).throw(AssertionError("fundamental analyst must not run")),
        )
    else:
        monkeypatch.setattr(
            workflow_module,
            "build_fundamental_analysis_request",
            lambda *a, **k: observed_threads.append(threading.get_ident()) or _fundamental_request(),
        )
        monkeypatch.setattr(
            workflow_module,
            "FundamentalAnalyst",
            lambda: SimpleNamespace(assess=lambda req: _fundamental_assessment(adjustment=-2)),
        )

    if technical_mode == "off":
        monkeypatch.setattr(
            workflow_module,
            "build_technical_analysis_request",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("technical builder must not run")),
        )
        monkeypatch.setattr(
            workflow_module,
            "TechnicalAnalyst",
            lambda: (_ for _ in ()).throw(AssertionError("technical analyst must not run")),
        )
    else:
        monkeypatch.setattr(
            workflow_module,
            "build_technical_analysis_request",
            lambda *a, **k: observed_threads.append(threading.get_ident()) or _technical_request(),
        )
        monkeypatch.setattr(
            workflow_module,
            "TechnicalAnalyst",
            lambda: SimpleNamespace(assess=lambda req: _technical_assessment(adjustment=-2)),
        )

    response = workflow.analyze("NVDA")

    assert observed_threads == [caller_thread_id]
    assert (response.fundamental_agent_effect is None) == (fundamental_mode == "off")
    assert (response.technical_agent_effect is None) == (technical_mode == "off")
    step_names = [step.agent_name for step in response.trace.steps]
    assert ("fundamental_context_builder" in step_names) == (fundamental_mode != "off")
    assert ("technical_context_builder" in step_names) == (technical_mode != "off")
    assert check_downgrade_only_behavior(baseline, response) == []
    assert check_fundamental_agent_authority(response) == []
    assert check_technical_agent_effect_rank(response) == []
    assert check_critic_step_zero_redundant_tokens(response.trace) == []
    assert check_specialist_sequence(response.trace, allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS) == []


def test_reversed_completion_order_produces_identical_effects_hashes_and_trace_order(monkeypatch):
    settings = _configure_settings(monkeypatch, fundamental_mode="enforced", technical_mode="enforced")
    baseline = _trade_baseline(checklist_score=8)

    def _run_ordered(*, finish_first: str) -> tuple[AnalyzeResponse, AgenticAnalysisWorkflow, list[str]]:
        workflow = _build_offline_workflow(
            monkeypatch,
            settings=settings,
            baseline=baseline,
            memo_decision="no_trade",
            memo_confidence=0.35,
        )
        monkeypatch.setattr(
            workflow_module,
            "build_fundamental_analysis_request",
            lambda ticker, run_id, as_of=None, engine=None: _fundamental_request(run_id=run_id),
        )
        monkeypatch.setattr(
            workflow_module,
            "build_technical_analysis_request",
            lambda ticker, run_id, as_of=None, engine=None: _technical_request(run_id=run_id),
        )
        first_done = threading.Event()
        completion_order: list[str] = []
        order_lock = threading.Lock()

        class _OrderedFundamental:
            def assess(self, request):
                del request
                if finish_first == "technical":
                    assert first_done.wait(timeout=2.0)
                with order_lock:
                    completion_order.append("fundamental")
                if finish_first == "fundamental":
                    first_done.set()
                return _fundamental_assessment(adjustment=-2)

        class _OrderedTechnical:
            def assess(self, request):
                del request
                if finish_first == "fundamental":
                    assert first_done.wait(timeout=2.0)
                with order_lock:
                    completion_order.append("technical")
                if finish_first == "technical":
                    first_done.set()
                return _technical_assessment(adjustment=-2)

        monkeypatch.setattr(workflow_module, "FundamentalAnalyst", _OrderedFundamental)
        monkeypatch.setattr(workflow_module, "TechnicalAnalyst", _OrderedTechnical)
        response = workflow.analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=date(2026, 9, 8)))
        return response, workflow, completion_order

    resp_fund_first, wf_fund_first, order_1 = _run_ordered(finish_first="fundamental")
    resp_tech_first, wf_tech_first, order_2 = _run_ordered(finish_first="technical")

    assert order_1 == ["fundamental", "technical"]
    assert order_2 == ["technical", "fundamental"]
    assert resp_fund_first.fundamental_agent_effect == resp_tech_first.fundamental_agent_effect
    assert resp_fund_first.technical_agent_effect == resp_tech_first.technical_agent_effect
    assert resp_fund_first.decision == resp_tech_first.decision == "no_trade"
    assert resp_fund_first.confidence == resp_tech_first.confidence
    assert wf_fund_first.last_technical_assessment == wf_tech_first.last_technical_assessment
    assert wf_fund_first.last_technical_request.context_hash == wf_tech_first.last_technical_request.context_hash

    names_1 = [step.agent_name for step in resp_fund_first.trace.steps]
    names_2 = [step.agent_name for step in resp_tech_first.trace.steps]
    assert names_1 == names_2
    assert names_1[:6] == [
        "input_resolver",
        "deterministic_pipeline",
        "fundamental_context_builder",
        "fundamental_analyst",
        "technical_context_builder",
        "technical_analyst",
    ]
    specialist_sequences = [
        next(step.sequence for step in resp_tech_first.trace.steps if step.agent_name == name)
        for name in SPECIALIST_ORDER
    ]
    assert specialist_sequences == sorted(specialist_sequences)
    assert resp_tech_first.entry_range is None
    non_downgrade_violations = [
        violation
        for violation in evaluate_agentic_invariants(
            deterministic_response=baseline,
            agentic_response=resp_tech_first,
            trace=resp_tech_first.trace,
            allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
        )
        if violation.code not in {"risk_field_mismatch", "synthesizer_changed_risk_number", "memo_risk_block_mismatch"}
    ]
    assert non_downgrade_violations == []


def test_fundamental_fail_open_does_not_block_technical_enforcement(monkeypatch):
    settings = _configure_settings(monkeypatch, fundamental_mode="enforced", technical_mode="enforced")
    baseline = _trade_baseline(checklist_score=8)
    workflow = _build_offline_workflow(
        monkeypatch,
        settings=settings,
        baseline=baseline,
        memo_decision="watchlist",
        memo_confidence=0.6,
    )
    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", lambda *a, **k: _fundamental_request())
    monkeypatch.setattr(workflow_module, "build_technical_analysis_request", lambda *a, **k: _technical_request())

    class _FailingPort:
        model_name = "failing-fundamental"

        def generate(self, prompt: str) -> str:
            del prompt
            raise RetryableModelError("upstream timeout")

    monkeypatch.setattr(workflow_module, "FundamentalAnalyst", lambda: FundamentalAnalyst(model_port=_FailingPort()))
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(assess=lambda req: _technical_assessment(adjustment=-2)),
    )

    response = workflow.analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=date(2026, 9, 8)))

    assert response.fundamental_agent_effect is not None
    assert response.fundamental_agent_effect.accepted is False
    assert response.fundamental_agent_effect.applied_adjustment == 0
    assert response.fundamental_agent_effect.final_decision == "trade"
    assert response.technical_agent_effect is not None
    assert response.technical_agent_effect.baseline_decision == "trade"
    assert response.technical_agent_effect.applied_adjustment == -2
    assert response.technical_agent_effect.final_decision == "watchlist"
    assert response.decision == "watchlist"
    assert response.entry_range is None
    fundamental_step = next(step for step in response.trace.steps if step.agent_name == "fundamental_analyst")
    assert fundamental_step.status == "degraded"
    non_downgrade_violations = [
        violation
        for violation in evaluate_agentic_invariants(
            deterministic_response=baseline,
            agentic_response=response,
            trace=response.trace,
            allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
        )
        if violation.code not in {"risk_field_mismatch", "synthesizer_changed_risk_number", "memo_risk_block_mismatch"}
    ]
    assert non_downgrade_violations == []


def test_technical_insufficient_data_preserves_fundamental_downgrade(monkeypatch):
    settings = _configure_settings(monkeypatch, fundamental_mode="enforced", technical_mode="enforced")
    baseline = _trade_baseline(checklist_score=8)
    workflow = _build_offline_workflow(
        monkeypatch,
        settings=settings,
        baseline=baseline,
        memo_decision="watchlist",
        memo_confidence=0.6,
    )
    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", lambda *a, **k: _fundamental_request())
    monkeypatch.setattr(
        workflow_module,
        "build_technical_analysis_request",
        lambda *a, **k: _technical_request(bar_count=5, bars_stale=True),
    )

    class _NeverCalledTechnicalPort:
        model_name = "never-called"

        def generate(self, prompt: str) -> str:
            raise AssertionError("Technical model should not be called when coverage is insufficient.")

    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(assess=lambda req: _fundamental_assessment(adjustment=-2)),
    )
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: TechnicalAnalyst(model_port=_NeverCalledTechnicalPort()),
    )

    response = workflow.analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=date(2026, 9, 8)))

    assert response.fundamental_agent_effect is not None
    assert response.fundamental_agent_effect.final_decision == "watchlist"
    assert response.technical_agent_effect is not None
    assert response.technical_agent_effect.assessment_status == "insufficient_data"
    assert response.technical_agent_effect.baseline_decision == "watchlist"
    assert response.technical_agent_effect.applied_adjustment == 0
    assert response.technical_agent_effect.final_decision == "watchlist"
    assert response.decision == "watchlist"
    assert response.entry_range == baseline.entry_range
    technical_step = next(step for step in response.trace.steps if step.agent_name == "technical_analyst")
    assert technical_step.status == "degraded"
    assert (
        evaluate_agentic_invariants(
            deterministic_response=baseline,
            agentic_response=response,
            trace=response.trace,
            allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
        )
        == []
    )


def test_worker_exception_propagates_after_peer_thread_joins(monkeypatch):
    settings = _configure_settings(monkeypatch, fundamental_mode="enforced", technical_mode="enforced")
    workflow = _build_offline_workflow(monkeypatch, settings=settings, baseline=_trade_baseline())
    peer_finished = threading.Event()

    def _failing_fundamental(*_args, **_kwargs):
        raise RuntimeError("boom_in_fundamental_builder")

    def _slow_technical(*_args, **_kwargs):
        time.sleep(0.06)
        peer_finished.set()
        return _technical_request()

    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", _failing_fundamental)
    monkeypatch.setattr(workflow_module, "build_technical_analysis_request", _slow_technical)
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(assess=lambda req: _technical_assessment(adjustment=0)),
    )

    with pytest.raises(RuntimeError, match="boom_in_fundamental_builder"):
        workflow.analyze("NVDA")

    assert peer_finished.is_set()


def test_effective_as_of_resolved_once_when_request_date_is_none(monkeypatch):
    settings = _configure_settings(monkeypatch, fundamental_mode="enforced", technical_mode="enforced")
    workflow = _build_offline_workflow(monkeypatch, settings=settings, baseline=_trade_baseline())
    seen_as_ofs: dict[str, date | None] = {}

    def _capture_fundamental(ticker, run_id, as_of=None, engine=None):
        del ticker, run_id, engine
        seen_as_ofs["fundamental"] = as_of
        return _fundamental_request()

    def _capture_technical(ticker, run_id, as_of=None, engine=None):
        del ticker, run_id, engine
        seen_as_ofs["technical"] = as_of
        return _technical_request()

    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", _capture_fundamental)
    monkeypatch.setattr(workflow_module, "build_technical_analysis_request", _capture_technical)
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(assess=lambda req: _fundamental_assessment(adjustment=0)),
    )
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(assess=lambda req: _technical_assessment(adjustment=0)),
    )

    response = workflow.analyze("NVDA")

    assert isinstance(seen_as_ofs["fundamental"], date)
    assert seen_as_ofs["fundamental"] == seen_as_ofs["technical"]
    assert response.trace.as_of_date is None


def test_concurrent_specialists_bound_db_pool_checkouts_and_close_sessions_before_llm(db_engine, monkeypatch):
    ticker = "PARDB"
    as_of_date = date(2026, 9, 8)
    _seed_full_security(db_engine, ticker=ticker, as_of_date=as_of_date)

    settings = _configure_settings(monkeypatch, fundamental_mode="enforced", technical_mode="enforced")
    monkeypatch.setattr(db_session_module, "get_settings", lambda: settings)
    pooled_engine = db_session_module._create_engine(db_engine.url.render_as_string(hide_password=False))

    checkout_lock = threading.Lock()
    state = {"checked_out": 0, "max_checked_out": 0}

    @event.listens_for(pooled_engine, "checkout")
    def _on_checkout(_dbapi_conn, _conn_record, _conn_proxy):
        with checkout_lock:
            state["checked_out"] += 1
            if state["checked_out"] > state["max_checked_out"]:
                state["max_checked_out"] = state["checked_out"]

    @event.listens_for(pooled_engine, "checkin")
    def _on_checkin(_dbapi_conn, _conn_record):
        with checkout_lock:
            state["checked_out"] -= 1

    try:
        monkeypatch.setattr(
            fundamental_context_module,
            "_build_embedding_client",
            lambda: MockEmbeddingClient(dimension=768),
        )
        workflow = _build_offline_workflow(
            monkeypatch,
            settings=settings,
            baseline=_trade_baseline(),
            engine=pooled_engine,
            memo_decision="watchlist",
        )
        monkeypatch.setattr(
            workflow_module,
            "resolve_ticker",
            lambda ref: SimpleNamespace(is_valid=True, ticker=ticker, error=None),
        )

        barrier = threading.Barrier(2, timeout=2.0)
        checked_out_during_assess: list[int] = []

        class _PoolCheckingFundamental:
            def assess(self, request):
                barrier.wait()
                checked_out_during_assess.append(pooled_engine.pool.checkedout())
                return _fundamental_assessment(adjustment=-1).model_copy(
                    update={
                        "response": FundamentalAssessmentResponse(
                            run_id=request.run_id,
                            context_hash=request.context_hash,
                            status="completed",
                            overall_signal="neutral",
                            proposed_score_adjustment=0,
                            findings=[],
                            contradictions=[],
                            material_red_flags=[],
                            evidence_coverage=0.5,
                            missing_information=[],
                            summary="Neutral.",
                        )
                    }
                )

        class _PoolCheckingTechnical:
            def assess(self, request):
                barrier.wait()
                checked_out_during_assess.append(pooled_engine.pool.checkedout())
                return _technical_assessment(adjustment=0).model_copy(
                    update={
                        "response": TechnicalAssessmentResponse(
                            run_id=request.run_id,
                            context_hash=request.context_hash,
                            status="completed",
                            overall_signal="neutral",
                            trend_state="range",
                            momentum_state="stable",
                            proposed_score_adjustment=0,
                            findings=[],
                            contradictions=[],
                            risk_flags=[],
                            missing_information=[],
                            summary="Neutral.",
                        )
                    }
                )

        monkeypatch.setattr(workflow_module, "FundamentalAnalyst", _PoolCheckingFundamental)
        monkeypatch.setattr(workflow_module, "TechnicalAnalyst", _PoolCheckingTechnical)

        response = workflow.analyze(ticker, request=AnalyzeRequest(ticker=ticker, as_of_date=as_of_date))

        assert state["max_checked_out"] <= 2
        assert checked_out_during_assess == [0, 0]
        assert pooled_engine.pool.checkedout() == 0
        assert response.fundamental_agent_effect is not None
        assert response.technical_agent_effect is not None
    finally:
        pooled_engine.dispose()


def test_concurrent_specialists_release_db_connections_on_worker_error(db_engine, monkeypatch):
    ticker = "PARER"
    as_of_date = date(2026, 9, 8)
    _seed_full_security(db_engine, ticker=ticker, as_of_date=as_of_date)

    settings = _configure_settings(monkeypatch, fundamental_mode="enforced", technical_mode="enforced")
    monkeypatch.setattr(db_session_module, "get_settings", lambda: settings)
    pooled_engine = db_session_module._create_engine(db_engine.url.render_as_string(hide_password=False))

    try:
        monkeypatch.setattr(
            fundamental_context_module,
            "_build_embedding_client",
            lambda: MockEmbeddingClient(dimension=768),
        )
        workflow = _build_offline_workflow(
            monkeypatch,
            settings=settings,
            baseline=_trade_baseline(),
            engine=pooled_engine,
            memo_decision="watchlist",
        )
        monkeypatch.setattr(
            workflow_module,
            "resolve_ticker",
            lambda ref: SimpleNamespace(is_valid=True, ticker=ticker, error=None),
        )
        monkeypatch.setattr(
            workflow_module,
            "FundamentalAnalyst",
            lambda: SimpleNamespace(
                assess=lambda req: (_ for _ in ()).throw(RuntimeError("boom_after_fundamental_db_reads"))
            ),
        )
        monkeypatch.setattr(
            workflow_module,
            "TechnicalAnalyst",
            lambda: SimpleNamespace(
                assess=lambda req: _technical_assessment(adjustment=0).model_copy(
                    update={
                        "response": TechnicalAssessmentResponse(
                            run_id=req.run_id,
                            context_hash=req.context_hash,
                            status="completed",
                            overall_signal="neutral",
                            trend_state="range",
                            momentum_state="stable",
                            proposed_score_adjustment=0,
                            findings=[],
                            contradictions=[],
                            risk_flags=[],
                            missing_information=[],
                            summary="Neutral.",
                        )
                    }
                )
            ),
        )

        with pytest.raises(RuntimeError, match="boom_after_fundamental_db_reads"):
            workflow.analyze(ticker, request=AnalyzeRequest(ticker=ticker, as_of_date=as_of_date))

        assert pooled_engine.pool.checkedout() == 0
    finally:
        pooled_engine.dispose()


def test_concurrent_context_builders_match_sequential_context_hashes(db_engine, monkeypatch):
    ticker = "PARHS"
    as_of_date = date(2026, 9, 8)
    _seed_full_security(db_engine, ticker=ticker, as_of_date=as_of_date)

    monkeypatch.setattr(
        fundamental_context_module,
        "_build_embedding_client",
        lambda: MockEmbeddingClient(dimension=768),
    )
    seq_fundamental = build_fundamental_analysis_request(
        ticker,
        "seq-run",
        as_of=as_of_date,
        engine=db_engine,
    )
    seq_technical = build_technical_analysis_request(
        ticker,
        "seq-run",
        as_of=as_of_date,
        engine=db_engine,
    )

    settings = _configure_settings(monkeypatch, fundamental_mode="enforced", technical_mode="enforced")
    workflow = _build_offline_workflow(
        monkeypatch,
        settings=settings,
        baseline=_trade_baseline(),
        engine=db_engine,
        memo_decision="watchlist",
    )
    monkeypatch.setattr(
        workflow_module,
        "resolve_ticker",
        lambda ref: SimpleNamespace(is_valid=True, ticker=ticker, error=None),
    )
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(
            assess=lambda req: _fundamental_assessment(adjustment=0).model_copy(
                update={
                    "response": FundamentalAssessmentResponse(
                        run_id=req.run_id,
                        context_hash=req.context_hash,
                        status="completed",
                        overall_signal="neutral",
                        proposed_score_adjustment=0,
                        findings=[],
                        contradictions=[],
                        material_red_flags=[],
                        evidence_coverage=0.5,
                        missing_information=[],
                        summary="Neutral.",
                    )
                }
            )
        ),
    )
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(
            assess=lambda req: _technical_assessment(adjustment=0).model_copy(
                update={
                    "response": TechnicalAssessmentResponse(
                        run_id=req.run_id,
                        context_hash=req.context_hash,
                        status="completed",
                        overall_signal="neutral",
                        trend_state="range",
                        momentum_state="stable",
                        proposed_score_adjustment=0,
                        findings=[],
                        contradictions=[],
                        risk_flags=[],
                        missing_information=[],
                        summary="Neutral.",
                    )
                }
            )
        ),
    )

    response = workflow.analyze(ticker, request=AnalyzeRequest(ticker=ticker, as_of_date=as_of_date))

    fund_builder_step = next(step for step in response.trace.steps if step.agent_name == "fundamental_context_builder")
    tech_builder_step = next(step for step in response.trace.steps if step.agent_name == "technical_context_builder")
    assert fund_builder_step.output["context_hash"] == seq_fundamental.context_hash
    assert tech_builder_step.output["context_hash"] == seq_technical.context_hash
    assert workflow.last_technical_request is not None
    assert workflow.last_technical_request.context_hash == seq_technical.context_hash


def _seed_full_security(db_engine, *, ticker: str, as_of_date: date) -> Security:
    with Session(db_engine) as session:
        security = Security(ticker=ticker, name=f"{ticker} Corp.", exchange="NASDAQ", sector_tag="ai")
        session.add(security)
        session.commit()
        session.refresh(security)

    upsert_fundamental(
        Fundamental(
            security_id=security.id,
            as_of_date=date(2026, 6, 30),
            revenue_growth=Decimal("0.21"),
            fcf=Decimal("1200"),
            debt_to_equity=Decimal("0.40"),
            eps_trend=Decimal("0.08"),
            margins=Decimal("0.32"),
            raw_payload={"source": "seed"},
        ),
        engine=db_engine,
    )
    upsert_fundamental_observations(
        [
            FundamentalObservation(
                security_id=security.id,
                metric_name="revenue",
                value=Decimal("500"),
                unit="USD",
                period_start=date(2026, 4, 1),
                period_end=date(2026, 6, 30),
                fiscal_year=2026,
                fiscal_period="Q2",
                form_type="10-Q",
                filed_at=date(2026, 7, 25),
                accession_number=f"{ticker}-rev-1",
                source_concept="Revenue",
                source_url=f"sec://{ticker}/rev/1",
                is_derived=False,
            )
        ],
        engine=db_engine,
    )
    start_date = as_of_date - timedelta(days=69)
    bars = [
        PriceBar(
            security_id=security.id,
            bar_date=start_date + timedelta(days=idx),
            open=Decimal(99 + idx),
            high=Decimal(101 + idx),
            low=Decimal(98 + idx),
            close=Decimal(100 + idx),
            volume=1_000 + idx,
        )
        for idx in range(70)
    ]
    upsert_price_bars(bars, engine=db_engine)
    upsert_technical_feature(
        TechnicalFeature(
            security_id=security.id,
            as_of_date=as_of_date,
            rsi_14=Decimal("55.0000"),
            sma_50=Decimal("140.0000"),
            sma_200=Decimal("120.0000"),
            volume_trend=Decimal("1.1000"),
        ),
        engine=db_engine,
    )
    with Session(db_engine) as session:
        session.add(MacroDaily(obs_date=as_of_date, vix=Decimal("18.500")))
        session.add(
            DocumentChunk(
                ticker=ticker,
                source_type=SourceType.filing_business,
                document_id=f"{ticker}-doc-1",
                source_url=f"https://example.com/{ticker}/1",
                publisher="SEC",
                title=f"{ticker} 10-Q",
                source_quality_tier=SourceQualityTier.primary_regulatory,
                source_hash=f"{ticker}-hash-1",
                published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                chunk_index=0,
                text="Recurring enterprise demand continues.",
                embedding=[0.0] * 768,
            )
        )
        session.commit()
    return security


def test_blackboard_off_enforced_preserves_specialist_order_via_real_adk_runner(monkeypatch):
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part

    from agents.orchestration.blackboard import (
        ANALYSIS_RESPONSE_KEY,
        BlackboardCriticAgent,
        BlackboardFundamentalAgent,
        BlackboardPolicyFoldAgent,
        BlackboardRuntime,
        BlackboardSynthesizerAgent,
        BlackboardTechnicalAgent,
        CrossDomainTensionAgent,
    )
    from agents.orchestration.registry import build_root_agent
    from agents.orchestration.trace_recorder import TraceRecorder

    _poison_network(monkeypatch)
    baseline = _trade_baseline(checklist_score=8)
    runtime = BlackboardRuntime(
        embedding_client_provider=lambda: None,
        fundamental_mode="off",
        technical_mode="enforced",
        synthesizer_contract="typed",
        critic_mode="off",
        analyze_request_fn=lambda req, engine=None: baseline.model_copy(deep=True),
        build_technical_request_fn=lambda ticker, run_id, as_of=None, engine=None: _technical_request(run_id=run_id),
        technical_analyst_factory=lambda: SimpleNamespace(assess=lambda req: _technical_assessment(adjustment=-2)),
        decision_synthesizer_provider=lambda: DecisionSynthesizer(
            _ScriptedSynthesizerModel(
                {
                    "proposed_decision": "watchlist",
                    "confidence": 0.55,
                    "thesis": "Technical downgrade applied.",
                    "key_reasons": [],
                    "key_risks": [],
                    "what_would_change": [],
                }
            ),
            max_retries=0,
        ),
    )
    specialists = {
        "fundamental_analyst": BlackboardFundamentalAgent(name="fundamental_analyst", runtime=runtime),
        "technical_analyst": BlackboardTechnicalAgent(name="technical_analyst", runtime=runtime),
        "specialist_policy_fold": BlackboardPolicyFoldAgent(name="specialist_policy_fold", runtime=runtime),
        "cross_domain_tension": CrossDomainTensionAgent(name="cross_domain_tension", runtime=runtime),
        "decision_synthesizer": BlackboardSynthesizerAgent(name="decision_synthesizer", runtime=runtime),
        "critic_guardrail": BlackboardCriticAgent(name="critic_guardrail", runtime=runtime),
    }
    root_agent = build_root_agent(
        specialists,
        fundamental_mode="off",
        technical_mode="enforced",
        use_blackboard=True,
    )
    assert [agent.name for agent in root_agent.sub_agents[:2]] == ["fundamental_analyst", "technical_analyst"]

    import asyncio

    session_service = InMemorySessionService()
    session = asyncio.run(session_service.create_session(app_name="forseti", user_id="NVDA"))
    runner = Runner(app_name="forseti", agent=root_agent, session_service=session_service)
    events = list(
        runner.run(
            user_id="NVDA",
            session_id=session.id,
            new_message=Content(role="user", parts=[Part(text="Analyze ticker NVDA")]),
        )
    )
    recorder = TraceRecorder()
    for adk_event in events:
        if adk_event.author in SPECIALIST_ORDER:
            recorder.record_completed(adk_event.author, output={"status": "completed"})
    final_session = asyncio.run(session_service.get_session(app_name="forseti", user_id="NVDA", session_id=session.id))
    assert final_session is not None
    assert final_session.state[ANALYSIS_RESPONSE_KEY]["decision"] == "watchlist"
    trace = workflow_module.AnalysisTrace(run_id="run-1", ticker="NVDA", steps=recorder.steps())
    assert check_specialist_sequence(trace, allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS) == []
