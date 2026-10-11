from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlmodel import Session

import agents.orchestration.workflow as workflow_module
from agents.config import load_agent_config
from agents.orchestration.registry import AgentRegistry
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.db.models import EarningsEvent, Fundamental, MacroDaily, PriceBar, Security, TechnicalFeature
from app.db.repository import (
    upsert_earnings_event,
    upsert_fundamental,
    upsert_macro_daily,
    upsert_price_bars,
    upsert_technical_feature,
)
from app.domain.fundamentals import DeterministicFundamentalAnalysis, FundamentalMetricRef, FundamentalRuleResult
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse, DecisionDiagnosis
from app.schemas.critique import CritiqueRequest, DraftRecommendation
from app.schemas.fundamentals import (
    EvidenceChunkInput,
    EvidenceQuestionCoverage,
    FundamentalAnalysisRequest,
    FundamentalContextCoverage,
    MetricSeries,
    MetricSeriesPoint,
)
from app.services.critic import Critic, ModelOutput as CriticModelOutput, RetryableModelError as CriticRetryableError
from app.services.fundamental_analyst import (
    FundamentalAnalyst,
    ModelOutput as FundamentalModelOutput,
    RetryableModelError as FundamentalRetryableError,
)
from app.settings import Settings
from app.trust.invariants import check_downgrade_only_behavior, check_risk_field_equality


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


class _FundamentalSequenceModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.model_name = "fake-fundamental"

    def generate(self, prompt: str) -> FundamentalModelOutput:
        del prompt
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return FundamentalModelOutput(text=output, token_usage={"total_token_count": 7})


class _CriticSequenceModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.model_name = "fake-critic"

    def generate(self, prompt: str) -> CriticModelOutput:
        del prompt
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return CriticModelOutput(text=output, token_usage={"total_token_count": 5})


def _deterministic_trade_response() -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker="NVDA",
        decision="trade",
        confidence=0.82,
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
            rule_id="score_above_trade",
            detail="score 9/11, missing: none",
            checklist_score=9,
            debug_reason="checklist/score_above_trade: score 9/11, missing: none",
        ),
    )


def _event(
    author: str,
    *,
    tool_calls: tuple[str, ...] = (),
    text: str = "",
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
        usage_metadata=SimpleNamespace(),
        error_code=error_code,
        error_message=error_message,
    )


def _workflow(
    monkeypatch,
    *,
    events=(),
    deterministic_response: AnalyzeResponse | None = None,
    engine=None,
    with_retriever: bool = False,
    embedding_client=None,
):
    monkeypatch.setattr(workflow_module, "resolve_ticker", lambda ticker_reference: _Resolved())
    if deterministic_response is not None:
        monkeypatch.setattr(
            workflow_module,
            "analyze_request",
            lambda request, engine=None: deterministic_response.model_copy(deep=True),
        )
    monkeypatch.setattr(
        workflow_module,
        "build_agent_registry",
        lambda **kwargs: AgentRegistry(
            tools={"retriever": object()} if with_retriever else {},
            specialists={},
            root_agent=None,
        ),
    )
    monkeypatch.setattr(workflow_module, "build_critique_request", lambda *args, **kwargs: _critique_request(kwargs["draft"]))
    monkeypatch.setattr(workflow_module, "evaluate_deterministic_objections", lambda **kwargs: [])
    if engine is None:
        monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_persist_trace", lambda self, trace: None)

    def _runner(registry, ticker):
        del registry, ticker
        return iter(events)

    return AgenticAnalysisWorkflow(
        load_agent_config(Settings(_env_file=None)),
        engine=engine,
        runner_factory=_runner,
        llm_io_recorder=_NoopRecorder(),
        embedding_client=embedding_client,
    )


def _enable_modes(monkeypatch, *, fundamental_mode: str = "off", critic_mode: str = "off") -> None:
    monkeypatch.setattr(workflow_module, "_fundamental_agent_mode", lambda: fundamental_mode)
    monkeypatch.setattr(workflow_module, "_critic_mode", lambda: critic_mode)


def _assert_safe_result(
    baseline: AnalyzeResponse,
    response: AnalyzeResponse,
    *,
    expected_record: str,
) -> None:
    assert check_risk_field_equality(baseline, response) == []
    assert check_downgrade_only_behavior(baseline, response) == []
    assert _trace_step(response, expected_record) is not None


def _trace_step(response: AnalyzeResponse, agent_name: str):
    assert response.trace is not None
    for step in response.trace.steps:
        if step.agent_name == agent_name:
            return step
    return None


def _fundamental_request(*, evidence_chunks: list[EvidenceChunkInput] | None = None) -> FundamentalAnalysisRequest:
    metric_point = MetricSeriesPoint(
        metric_id="revenue:Q2:2026-06-30:USD:rev-1",
        value=Decimal("100"),
        unit="USD",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        fiscal_year=2026,
        fiscal_period="Q2",
        form_type="10-Q",
        filed_at=date(2026, 7, 20),
        accession_number="rev-1",
        source_concept="Revenue",
        source_url="sec://rev-1",
        is_derived=False,
    )
    chunks = evidence_chunks if evidence_chunks is not None else [
        EvidenceChunkInput(
            chunk_id=10,
            retrieval_question_id="growth_sustainability",
            source_type="filing_business",
            source_url="https://example.com/10",
            source_hash="hash-10",
            chunk_index=0,
            published_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
            quality_tier="primary",
            text="Revenue growth is supported by recurring demand.",
        )
    ]
    return FundamentalAnalysisRequest(
        run_id="run-1",
        context_hash="ctx-hash",
        ticker="NVDA",
        company_name="NVIDIA",
        sector="ai",
        currency="USD",
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        as_of_date=date(2026, 9, 8),
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
        metric_series=[MetricSeries(metric_name="revenue", annual=[], quarterly=[metric_point])],
        evidence_chunks=chunks,
        coverage=FundamentalContextCoverage(
            required_metrics_present=["revenue_growth"],
            required_metrics_missing=[],
            annual_period_counts={"revenue": 0},
            quarterly_period_counts={"revenue": 1},
            source_types_present=["filing_business"] if chunks else [],
            source_types_missing=[],
            newest_evidence_published_at=datetime(2026, 7, 21, tzinfo=timezone.utc) if chunks else None,
            evidence_stale=False,
            evidence_truncated=False,
            metric_series_truncated=False,
            selected_chunk_count=len(chunks),
            selected_character_count=sum(len(chunk.text) for chunk in chunks),
            question_coverage=[
                EvidenceQuestionCoverage(
                    question_id="growth_sustainability",
                    chunk_ids=[chunk.chunk_id for chunk in chunks],
                    source_types=["filing_business"] if chunks else [],
                    truncated=False,
                )
            ],
        ),
    )


def _fundamental_response_json(request: FundamentalAnalysisRequest) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "run_id": request.run_id,
            "context_hash": request.context_hash,
            "agent_name": "fundamental_analyst",
            "status": "completed",
            "overall_signal": "positive",
            "proposed_score_adjustment": 1,
            "findings": [
                {
                    "finding_id": "f1",
                    "category": "growth_quality",
                    "direction": "positive",
                    "materiality": "medium",
                    "claim": "Recurring demand supports growth.",
                    "metric_ids": ["revenue_growth:2026-06-30"],
                    "chunk_ids": [10],
                }
            ],
            "contradictions": [],
            "material_red_flags": [],
            "evidence_coverage": 0.75,
            "missing_information": [],
            "summary": "Incremental evidence supports a positive adjustment.",
        }
    )


def _critique_request(draft: DraftRecommendation) -> CritiqueRequest:
    return CritiqueRequest(
        run_id="critic-run",
        context_hash="critic-hash",
        ticker="NVDA",
        as_of_date=date(2026, 9, 8),
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        draft=draft,
        evidence_chunks=[
            EvidenceChunkInput(
                chunk_id=10,
                retrieval_question_id="growth_sustainability",
                source_type="filing_business",
                source_url="https://example.com/10",
                source_hash="hash-10",
                chunk_index=0,
                published_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
                quality_tier="primary",
                text="Revenue growth remains healthy.",
            )
        ],
        deterministic_warnings=[],
        analyst_views=[],
        allowed_actions=["accept", "downgrade_confidence", "downgrade_decision", "force_no_trade", "request_revision"],
    )


def _critic_response_json(request: CritiqueRequest) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "run_id": request.run_id,
            "context_hash": request.context_hash,
            "agent_name": "critic_guardrail",
            "status": "completed",
            "verdict": "accept",
            "objections": [],
            "proposed_decision": None,
            "proposed_confidence_penalty": 0.0,
            "revision_instructions": "",
            "summary": "No issues.",
        }
    )


def test_fundamental_timeout_falls_back_to_deterministic(monkeypatch):
    _enable_modes(monkeypatch, fundamental_mode="enforced")
    baseline = _deterministic_trade_response()
    request = _fundamental_request()
    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", lambda *args, **kwargs: request)
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: FundamentalAnalyst(
            model_port=_FundamentalSequenceModel([FundamentalRetryableError("timeout")]),
            max_retries=0,
        ),
    )

    response = _workflow(monkeypatch, deterministic_response=baseline).analyze("NVDA")

    _assert_safe_result(baseline, response, expected_record="fundamental_analyst")
    assert "provider_error:timeout" in response.fundamental_agent_effect.reason_codes
    assert response.fundamental_agent_effect.applied_adjustment == 0
    assert _trace_step(response, "fundamental_analyst").status == "degraded"


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        ("not json", "malformed_json"),
        (
            json.dumps(
                {
                    **json.loads(_fundamental_response_json(_fundamental_request())),
                    "findings": [
                        {
                            "finding_id": "f1",
                            "category": "growth_quality",
                            "direction": "positive",
                            "materiality": "high",
                            "claim": "Uncited claim.",
                            "metric_ids": [],
                            "chunk_ids": [],
                        }
                    ],
                }
            ),
            "schema_validation_failed",
        ),
        (
            json.dumps(
                {
                    **json.loads(_fundamental_response_json(_fundamental_request())),
                    "stop_loss": 1.0,
                }
            ),
            "forbidden_response_field:stop_loss",
        ),
    ],
)
def test_fundamental_invalid_payloads_are_neutral(monkeypatch, payload: str, expected_reason: str):
    _enable_modes(monkeypatch, fundamental_mode="enforced")
    baseline = _deterministic_trade_response()
    request = _fundamental_request()
    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", lambda *args, **kwargs: request)
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: FundamentalAnalyst(model_port=_FundamentalSequenceModel([payload]), max_retries=0),
    )

    response = _workflow(monkeypatch, deterministic_response=baseline).analyze("NVDA")

    _assert_safe_result(baseline, response, expected_record="fundamental_analyst")
    assert expected_reason in response.fundamental_agent_effect.reason_codes
    assert response.fundamental_agent_effect.applied_adjustment == 0


def test_technical_exception_is_warned_and_degraded(monkeypatch):
    _enable_modes(monkeypatch)
    baseline = _deterministic_trade_response()
    response = _workflow(
        monkeypatch,
        deterministic_response=baseline,
        events=[
            _event(
                "technical_analyst",
                error_code="TOOL_ERROR",
                error_message="Technical analyst crashed.",
            )
        ],
    ).analyze("NVDA")

    _assert_safe_result(baseline, response, expected_record="technical_analyst")
    assert "agent_narration_degraded: Technical analyst crashed." in response.warnings
    assert _trace_step(response, "technical_analyst").status == "degraded"


def test_technical_empty_output_keeps_safe_baseline(monkeypatch):
    _enable_modes(monkeypatch)
    baseline = _deterministic_trade_response()
    response = _workflow(
        monkeypatch,
        deterministic_response=baseline,
        events=[_event("technical_analyst", text="")],
    ).analyze("NVDA")

    _assert_safe_result(baseline, response, expected_record="technical_analyst")
    assert _trace_step(response, "technical_analyst").status == "completed"
    assert _trace_step(response, "technical_analyst").output is None
    assert _trace_step(response, "decision_synthesizer").status == "skipped"


def test_decision_synthesizer_exception_is_safe(monkeypatch):
    _enable_modes(monkeypatch)
    baseline = _deterministic_trade_response()
    response = _workflow(
        monkeypatch,
        deterministic_response=baseline,
        events=[
            _event(
                "decision_synthesizer",
                error_code="MODEL_ERROR",
                error_message="Decision synthesizer failed.",
            )
        ],
    ).analyze("NVDA")

    _assert_safe_result(baseline, response, expected_record="decision_synthesizer")
    assert "agent_narration_degraded: Decision synthesizer failed." in response.warnings
    assert _trace_step(response, "decision_synthesizer").status == "degraded"


def test_decision_synthesizer_tampered_numbers_are_rejected(monkeypatch):
    _enable_modes(monkeypatch)
    baseline = _deterministic_trade_response()
    response = _workflow(
        monkeypatch,
        deterministic_response=baseline,
        events=[
            _event(
                "decision_synthesizer",
                text='{"decision":"trade","confidence":0.9,"reasons":["upgrade"],"stop_loss":1.0}',
            )
        ],
    ).analyze("NVDA")

    _assert_safe_result(baseline, response, expected_record="decision_synthesizer")
    assert any(warning.startswith("guardrail_rejected: agent_output_changed_risk_value") for warning in response.warnings)
    assert _trace_step(response, "decision_synthesizer").status == "degraded"


@pytest.mark.parametrize(
    ("model_output", "expected_reason"),
    [
        (CriticRetryableError("timeout"), "provider_error:timeout"),
        ("not json", "malformed_json"),
    ],
)
def test_critic_failures_preserve_baseline(monkeypatch, model_output, expected_reason: str):
    _enable_modes(monkeypatch, critic_mode="enforced")
    baseline = _deterministic_trade_response()
    monkeypatch.setattr(
        workflow_module.AgenticAnalysisWorkflow,
        "_review_critique",
        lambda self, critique_request: Critic(
            model_port=_CriticSequenceModel([model_output]),
            max_retries=0,
        ).review(critique_request),
    )
    response = _workflow(
        monkeypatch,
        deterministic_response=baseline,
        events=[
            _event(
                "decision_synthesizer",
                text='{"decision":"trade","confidence":0.8,"reasons":["baseline"]}',
            ),
            _event("critic_guardrail", text="review"),
        ],
    ).analyze("NVDA")

    _assert_safe_result(baseline, response, expected_record="critic_guardrail")
    assert expected_reason in response.critic_effect.reason_codes
    assert _trace_step(response, "critic_guardrail").status == "degraded"


def test_retriever_unavailable_records_skip_warning(monkeypatch):
    _enable_modes(monkeypatch)
    baseline = _deterministic_trade_response()
    response = _workflow(monkeypatch, deterministic_response=baseline).analyze("NVDA")

    _assert_safe_result(baseline, response, expected_record="retriever")
    assert "retriever_tool_unavailable" in response.warnings
    retriever_step = _trace_step(response, "retriever")
    assert retriever_step.status == "skipped"
    assert retriever_step.output == {"reason": "retriever_tool_unavailable"}


def test_zero_retrieval_chunks_downgrade_to_neutral_fundamental_effect(monkeypatch):
    _enable_modes(monkeypatch, fundamental_mode="enforced")
    baseline = _deterministic_trade_response()
    empty_request = _fundamental_request(evidence_chunks=[])
    monkeypatch.setattr(workflow_module, "build_fundamental_analysis_request", lambda *args, **kwargs: empty_request)

    response = _workflow(
        monkeypatch,
        deterministic_response=baseline,
        with_retriever=True,
        embedding_client=object(),
    ).analyze("NVDA")

    _assert_safe_result(baseline, response, expected_record="fundamental_analyst")
    assert "insufficient_evidence" in response.fundamental_agent_effect.reason_codes
    assert response.fundamental_agent_effect.assessment_status == "insufficient_data"
    assert _trace_step(response, "fundamental_analyst").status == "degraded"


def test_agentic_mode_stale_price_data_cannot_be_upgraded_to_trade(monkeypatch, db_engine):
    _enable_modes(monkeypatch)
    as_of_date = date(2026, 1, 20)
    _seed_trade_candidate(
        db_engine,
        ticker="NVDA",
        as_of_date=as_of_date,
        latest_bar_date=as_of_date - timedelta(days=10),
        include_fundamental=True,
    )
    response = _workflow(
        monkeypatch,
        engine=db_engine,
        with_retriever=True,
        embedding_client=object(),
        events=[
            _event(
                "decision_synthesizer",
                text='{"decision":"trade","confidence":0.99,"reasons":["upgrade attempt"]}',
            )
        ],
    ).analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=as_of_date))

    assert response.decision == "watchlist"
    assert "stale_price_data" in response.warnings
    assert response.entry_range is None
    assert response.stop_loss is None


def test_agentic_mode_missing_price_data_never_yields_trade(monkeypatch, db_engine):
    _enable_modes(monkeypatch)
    as_of_date = date(2026, 1, 20)
    _seed_security(db_engine, "NVDA")
    response = _workflow(
        monkeypatch,
        engine=db_engine,
        with_retriever=True,
        embedding_client=object(),
        events=[
            _event(
                "decision_synthesizer",
                text='{"decision":"trade","confidence":0.99,"reasons":["upgrade attempt"]}',
            )
        ],
    ).analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=as_of_date))

    assert response.decision == "no_trade"
    assert response.warnings == ["no_price_data"]
    assert response.entry_range is None
    assert response.stop_loss is None


def test_agentic_mode_missing_fundamentals_stays_below_trade(monkeypatch, db_engine):
    _enable_modes(monkeypatch)
    as_of_date = date(2026, 1, 20)
    _seed_trade_candidate(
        db_engine,
        ticker="NVDA",
        as_of_date=as_of_date,
        latest_bar_date=as_of_date,
        include_fundamental=False,
    )
    response = _workflow(
        monkeypatch,
        engine=db_engine,
        with_retriever=True,
        embedding_client=object(),
        events=[
            _event(
                "decision_synthesizer",
                text='{"decision":"trade","confidence":0.99,"reasons":["upgrade attempt"]}',
            )
        ],
    ).analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=as_of_date))

    assert response.decision == "watchlist"
    assert "no_fundamentals" in response.warnings
    assert response.entry_range is None
    assert response.stop_loss is None


def _seed_security(engine, ticker: str) -> Security:
    with Session(engine) as session:
        security = Security(ticker=ticker, name=f"{ticker} Corp", exchange="NASDAQ", sector_tag="ai")
        session.add(security)
        session.commit()
        session.refresh(security)
        return security


def _seed_trade_candidate(
    engine,
    *,
    ticker: str,
    as_of_date: date,
    latest_bar_date: date,
    include_fundamental: bool,
) -> None:
    security = _seed_security(engine, ticker)
    upsert_price_bars(_bars(security.id, latest_bar_date), engine=engine)
    upsert_technical_feature(
        TechnicalFeature(
            security_id=security.id,
            as_of_date=as_of_date,
            rsi_14=Decimal("55"),
            sma_50=Decimal("95"),
            sma_200=Decimal("90"),
            volume_trend=Decimal("1.2"),
        ),
        engine=engine,
    )
    if include_fundamental:
        upsert_fundamental(
            Fundamental(
                security_id=security.id,
                as_of_date=as_of_date - timedelta(days=1),
                revenue_growth=Decimal("0.20"),
                fcf=Decimal("1000000"),
                debt_to_equity=Decimal("0.40"),
                eps_trend=Decimal("0.10"),
                margins=Decimal("0.25"),
                raw_payload={},
            ),
            engine=engine,
        )
    upsert_earnings_event(
        EarningsEvent(
            security_id=security.id,
            report_date=as_of_date + timedelta(days=30),
            confirmed=True,
        ),
        engine=engine,
    )
    upsert_macro_daily(MacroDaily(obs_date=as_of_date, vix=Decimal("18.0")), engine=engine)


def _bars(security_id: int, latest_bar_date: date) -> list[PriceBar]:
    bars: list[PriceBar] = []
    start_date = latest_bar_date - timedelta(days=249)
    for offset in range(250):
        bar_date = start_date + timedelta(days=offset)
        close = Decimal("100")
        bars.append(
            PriceBar(
                security_id=security_id,
                bar_date=bar_date,
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=1_000_000,
            )
        )
    return bars
