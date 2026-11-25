"""Deterministic offline tests for M4 structured Critic routing, post-revision gate, and M1 degraded invariants."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import socket
from types import SimpleNamespace
from typing import Any

import pytest

import agents.orchestration.blackboard as blackboard_module
import agents.orchestration.workflow as workflow_module
from agents.config import load_agent_config
from agents.orchestration.blackboard import (
    DRAFT_RECOMMENDATION_OUTPUT_KEY,
    DecisionSynthesis,
    _DecisionRevisionResult,
)
from agents.orchestration.workflow import MAX_CRITIC_REVISIONS, AgenticAnalysisWorkflow
from app.domain.fundamentals import DeterministicFundamentalAnalysis
from app.domain.technicals import DeterministicTechnicalAnalysis
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse, DecisionDiagnosis
from app.schemas.critique import (
    CritiqueObjection,
    CritiqueResponse,
    CritiqueResult,
    CritiqueRevisionTarget,
    CritiqueValidation,
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
from app.schemas.synthesis import (
    DecisionMemoResponse,
    DecisionMemoResult,
    DecisionMemoValidation,
)
from app.schemas.technicals import (
    TechnicalAnalysisRequest,
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
    TechnicalAssessmentValidation,
    TechnicalContextCoverage,
)
from app.services.decision_synthesizer import DecisionSynthesizer, ModelOutput, render_decision_memo
from app.settings import Settings
from app.trust.invariants import CRITIC_OFF_ALLOWED_SKIPS, RISK_FIELDS, evaluate_agentic_invariants

AS_OF = date(2026, 9, 8)
SNAPSHOT_AT = datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc)


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


def _poison_network(monkeypatch) -> None:
    def _reject(*_args, **_kwargs):
        raise AssertionError("Live network calls are forbidden.")

    monkeypatch.setattr(socket.socket, "connect", _reject)
    monkeypatch.setattr(socket.socket, "connect_ex", _reject)


def _baseline(*, decision: Any = "watchlist", with_risk: bool = False) -> AnalyzeResponse:
    risk_kwargs = (
        {
            "entry_range": (100.0, 102.0),
            "stop_loss": 95.0,
            "take_profit": (112.0, 118.0),
            "risk_reward": 2.5,
            "position_size_eur": 1000.0,
        }
        if with_risk
        else {}
    )
    return AnalyzeResponse(
        ticker="NVDA",
        decision=decision,
        confidence=0.70,
        reasons=["Deterministic baseline"],
        warnings=[],
        engine_version="v1.rules.0",
        trace_id="det-trace",
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="checklist_passed",
            detail="score 8/11" if decision == "trade" else "score 7/11",
            checklist_score=8 if decision == "trade" else 7,
            debug_reason=(
                "checklist/checklist_passed: score 8/11"
                if decision == "trade"
                else "checklist/checklist_passed: score 7/11"
            ),
        ),
        **risk_kwargs,
    )


def _fundamental_request(run_id: str = "run-1") -> FundamentalAnalysisRequest:
    return FundamentalAnalysisRequest(
        run_id=run_id,
        context_hash="fund-hash-1",
        ticker="NVDA",
        company_name="NVIDIA",
        sector="ai",
        currency="USD",
        snapshot_at=SNAPSHOT_AT,
        as_of_date=AS_OF,
        deterministic_result=DeterministicFundamentalAnalysis(
            ticker="NVDA",
            as_of_date=date(2026, 6, 30),
            score=0,
            maximum_score=6,
            rule_results=[],
            warnings=[],
            metrics=[],
        ),
        metric_series=[],
        evidence_chunks=[
            EvidenceChunkInput(
                chunk_id=10,
                retrieval_question_id="growth",
                source_type="filing_business",
                source_url="https://example.com/10",
                source_hash="hash-10",
                chunk_index=0,
                quality_tier="primary",
                text="Demand remains strong.",
            )
        ],
        coverage=FundamentalContextCoverage(
            required_metrics_present=[],
            required_metrics_missing=[],
            annual_period_counts={},
            quarterly_period_counts={},
            source_types_present=["filing_business"],
            source_types_missing=[],
            evidence_stale=False,
            evidence_truncated=False,
            metric_series_truncated=False,
            selected_chunk_count=1,
            selected_character_count=22,
            question_coverage=[
                EvidenceQuestionCoverage(
                    question_id="growth",
                    chunk_ids=[10],
                    source_types=["filing_business"],
                    truncated=False,
                )
            ],
        ),
    )


def _fundamental_assessment(run_id: str = "run-1") -> FundamentalAssessmentResult:
    return FundamentalAssessmentResult(
        response=FundamentalAssessmentResponse(
            run_id=run_id,
            context_hash="fund-hash-1",
            status="completed",
            overall_signal="positive",
            proposed_score_adjustment=0,
            findings=[
                CitedFinding(
                    finding_id="f1",
                    category="growth_quality",
                    direction="positive",
                    materiality="medium",
                    claim="Demand remains strong.",
                    chunk_ids=[10],
                )
            ],
            evidence_coverage=1.0,
            summary="Positive fundamentals.",
        ),
        validation=FundamentalAssessmentValidation(accepted=True),
        model_name="offline-fund",
        prompt_version="v1",
    )


def _technical_request(run_id: str = "run-1") -> TechnicalAnalysisRequest:
    return TechnicalAnalysisRequest(
        run_id=run_id,
        context_hash="tech-hash-1",
        ticker="NVDA",
        sector="ai",
        snapshot_at=SNAPSHOT_AT,
        as_of_date=AS_OF,
        deterministic_result=DeterministicTechnicalAnalysis(
            ticker="NVDA",
            as_of_date=AS_OF,
            score=0,
            maximum_score=5,
            rule_results=[],
            metrics=[],
        ),
        facts=[],
        series=[],
        coverage=TechnicalContextCoverage(
            bar_count=120,
            required_facts_missing=[],
            latest_bar_date=AS_OF,
            bars_stale=False,
            series_truncated=False,
        ),
    )


def _technical_assessment(run_id: str = "run-1") -> TechnicalAssessmentResult:
    return TechnicalAssessmentResult(
        response=TechnicalAssessmentResponse(
            run_id=run_id,
            context_hash="tech-hash-1",
            status="completed",
            overall_signal="positive",
            trend_state="uptrend",
            momentum_state="strengthening",
            proposed_score_adjustment=0,
            findings=[],
            summary="Positive technicals.",
        ),
        validation=TechnicalAssessmentValidation(accepted=True),
        model_name="offline-tech",
        prompt_version="v1",
    )


def _setup_blackboard_workflow(
    monkeypatch,
    *,
    baseline: AnalyzeResponse,
    synthesizer_contract: str = "typed",
    critic_mode: str = "enforced",
    synth_outputs: list[dict[str, Any]] | None = None,
):
    _poison_network(monkeypatch)
    settings = Settings(
        _env_file=None,
        PIPELINE_MODE="agentic",
        FUNDAMENTAL_AGENT_MODE="enforced",
        TECHNICAL_AGENT_MODE="enforced",
        SYNTHESIZER_CONTRACT=synthesizer_contract,
        CRITIC_MODE=critic_mode,
        AGENT_MAX_RETRIES=0,
    )
    monkeypatch.setattr(workflow_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        workflow_module,
        "resolve_ticker",
        lambda ref: SimpleNamespace(is_valid=True, ticker="NVDA", error=None),
    )
    monkeypatch.setattr(workflow_module, "analyze_request", lambda req, engine=None: baseline.model_copy(deep=True))
    monkeypatch.setattr(
        workflow_module,
        "build_fundamental_analysis_request",
        lambda ticker, run_id, **kwargs: _fundamental_request(run_id=run_id),
    )
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(assess=lambda req: _fundamental_assessment(run_id=req.run_id)),
    )
    monkeypatch.setattr(
        workflow_module,
        "build_technical_analysis_request",
        lambda ticker, run_id, **kwargs: _technical_request(run_id=run_id),
    )
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(assess=lambda req: _technical_assessment(run_id=req.run_id)),
    )
    monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_persist_trace", lambda self, trace: None)

    seen_synth_requests: list[dict[str, Any]] = []
    outputs = list(
        synth_outputs
        or [
            {
                "proposed_decision": baseline.decision,
                "confidence": baseline.confidence,
                "thesis": "Initial thesis.",
                "key_reasons": [],
                "key_risks": [],
                "what_would_change": [],
            },
            {
                "proposed_decision": baseline.decision,
                "confidence": baseline.confidence,
                "thesis": "Revised thesis addressing citation.",
                "key_reasons": [
                    {
                        "claim": "Demand remains strong.",
                        "metric_ids": [],
                        "chunk_ids": [10],
                        "warning_codes": [],
                    }
                ],
                "key_risks": [],
                "what_would_change": [],
            },
        ]
    )

    class _CapturingSynthModel:
        model_name = "offline-synth"

        def generate(self, prompt: str) -> ModelOutput:
            payload = json.loads(prompt.split("DecisionSynthesisRequest:\n", 1)[1])
            seen_synth_requests.append(payload)
            output = dict(outputs.pop(0))
            if "confidence" in output:
                output["confidence"] = min(output["confidence"], payload["max_confidence"])
            return ModelOutput(
                text=json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": payload["run_id"],
                        "context_hash": payload["context_hash"],
                        **output,
                    }
                ),
                token_usage={"total_token_count": 7},
            )

    synthesizer = DecisionSynthesizer(model_port=_CapturingSynthModel(), max_retries=0)
    workflow = AgenticAnalysisWorkflow(
        load_agent_config(settings),
        decision_synthesizer=synthesizer,
        llm_io_recorder=_NoopRecorder(),
    )
    return workflow, seen_synth_requests


def test_real_adk_blackboard_routes_structured_objection_ids_with_single_critic_call(monkeypatch):
    assert MAX_CRITIC_REVISIONS == 1
    baseline = _baseline(decision="watchlist")
    workflow, seen_synth_requests = _setup_blackboard_workflow(monkeypatch, baseline=baseline)

    critic_calls = []

    def _revise_once(self, critique_request):
        del self
        critic_calls.append(critique_request)
        return CritiqueResult(
            response=CritiqueResponse(
                run_id=critique_request.run_id,
                context_hash=critique_request.context_hash,
                status="completed",
                verdict="revise",
                objections=[
                    CritiqueObjection(
                        objection_id="model:cite:1",
                        category="unsupported_claim",
                        severity="medium",
                        source="model",
                        claim="Cite chunk 10 in key reasons.",
                        chunk_ids=[10],
                    )
                ],
                proposed_confidence_penalty=0.05,
                revision_instructions="Add chunk 10 citation.",
                revision_objection_ids=["model:cite:1"],
                revision_targets=[
                    CritiqueRevisionTarget(
                        objection_id="model:cite:1",
                        target="key_reasons",
                        action="add_citations",
                    )
                ],
                summary="Revise with citations.",
            ),
            validation=CritiqueValidation(accepted=True),
            token_usage={"prompt_token_count": 5, "total_token_count": 9},
            model_name="offline-critic",
            prompt_version="critic-guardrail.v1",
        )

    monkeypatch.setattr(AgenticAnalysisWorkflow, "_review_critique", _revise_once)

    response = workflow.analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=AS_OF))

    assert len(critic_calls) == 1
    assert len(seen_synth_requests) == 2
    initial_req, revision_req = seen_synth_requests
    assert initial_req["critic_objections"] == []
    assert initial_req["revision_objection_ids"] == []
    assert revision_req["revision_objection_ids"] == ["model:cite:1"]
    assert revision_req["revision_targets"] == [
        {"objection_id": "model:cite:1", "target": "key_reasons", "action": "add_citations"}
    ]
    assert [obj["objection_id"] for obj in revision_req["critic_objections"]] == ["model:cite:1"]

    assert response.critic_effect is not None
    assert response.critic_effect.revisions_requested == 1
    assert response.critic_effect.revisions_performed == 1
    assert response.memo is not None
    assert response.memo.thesis == "Revised thesis addressing citation."

    critic_step = next(step for step in response.trace.steps if step.agent_name == "critic_guardrail")
    assert critic_step.status == "completed"
    assert critic_step.output["revision_objection_ids"] == ["model:cite:1"]
    assert evaluate_agentic_invariants(
        deterministic_response=baseline,
        agentic_response=response,
        trace=response.trace,
    ) == []


def test_post_revision_gate_rejects_decision_upgrade_and_memo_claim_mismatch_with_zero_extra_critic_calls(monkeypatch):
    baseline = _baseline(decision="watchlist")
    workflow, _ = _setup_blackboard_workflow(monkeypatch, baseline=baseline)
    critic_calls = []

    def _revise_critic(self, critique_request):
        del self
        critic_calls.append(critique_request)
        return CritiqueResult(
            response=CritiqueResponse(
                run_id=critique_request.run_id,
                context_hash=critique_request.context_hash,
                status="completed",
                verdict="revise",
                objections=[
                    CritiqueObjection(
                        objection_id="model:1",
                        category="unsupported_claim",
                        severity="medium",
                        source="model",
                        claim="Cite chunk 10.",
                        chunk_ids=[10],
                    )
                ],
                revision_instructions="Cite chunk 10.",
                revision_objection_ids=["model:1"],
                summary="Revise.",
            ),
            validation=CritiqueValidation(accepted=True),
            model_name="offline-critic",
            prompt_version="critic-guardrail.v1",
        )

    def _bad_revision(self, **kwargs):
        del self
        typed_req = kwargs["typed_request"]
        bad_memo = DecisionMemoResponse(
            run_id=typed_req.run_id,
            context_hash=typed_req.context_hash,
            proposed_decision="trade",
            confidence=0.95,
            thesis="Decision: trade with stop loss 120.00.",
            key_reasons=[],
            key_risks=[],
            what_would_change=[],
        )
        return _DecisionRevisionResult(
            synthesis=DecisionSynthesis(decision="trade", confidence=0.95, reasons=["Upgrade attempt"]),
            raw_output=render_decision_memo(bad_memo, typed_req),
            token_usage={"total_token_count": 4},
            latency_ms=2.0,
            typed_request=typed_req,
            typed_result=DecisionMemoResult(
                response=bad_memo,
                validation=DecisionMemoValidation(accepted=False, reason_codes=["decision_promotion"]),
                rendered_memo=render_decision_memo(bad_memo, typed_req),
                used_fallback=False,
                model_name="offline-synth",
                prompt_version="decision-synthesizer.v1",
            ),
        )

    monkeypatch.setattr(AgenticAnalysisWorkflow, "_review_critique", _revise_critic)
    monkeypatch.setattr(AgenticAnalysisWorkflow, "_request_synthesis_revision", _bad_revision)

    response = workflow.analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=AS_OF))

    assert len(critic_calls) == 1
    assert response.decision == "watchlist"
    for field_name in RISK_FIELDS:
        assert getattr(response, field_name) == getattr(baseline, field_name)
    assert "critic_revision_failed" in response.warnings
    assert response.memo is not None
    assert response.memo.thesis == "Initial thesis."
    assert response.critic_effect is not None
    assert response.critic_effect.revisions_performed == 0
    assert "critic_revision_rejected_deterministic" in response.critic_effect.reason_codes
    assert "guardrail_rejected: decision_upgrade_attempt" in response.critic_effect.reason_codes
    critic_step = next(step for step in response.trace.steps if step.agent_name == "critic_guardrail")
    assert critic_step.status == "degraded"
    assert critic_step.output["reason"] == "critic_revision_failed"


def test_post_revision_gate_rejects_risk_mutation_and_corrupted_session_draft_with_zero_extra_critic_calls(monkeypatch):
    baseline = _baseline(decision="trade", with_risk=True)
    workflow, _ = _setup_blackboard_workflow(monkeypatch, baseline=baseline)
    critic_calls = []

    def _revise_critic(self, critique_request):
        del self
        critic_calls.append(critique_request)
        return CritiqueResult(
            response=CritiqueResponse(
                run_id=critique_request.run_id,
                context_hash=critique_request.context_hash,
                status="completed",
                verdict="revise",
                objections=[
                    CritiqueObjection(
                        objection_id="model:1",
                        category="unsupported_claim",
                        severity="medium",
                        source="model",
                        claim="Cite chunk 10.",
                        chunk_ids=[10],
                    )
                ],
                revision_instructions="Cite chunk 10.",
                revision_objection_ids=["model:1"],
                summary="Revise.",
            ),
            validation=CritiqueValidation(accepted=True),
            model_name="offline-critic",
            prompt_version="critic-guardrail.v1",
        )

    orig_run_bounded = blackboard_module.run_bounded_critic_revision

    def _corrupting_revision(**kwargs):
        session_state = kwargs["session_state"]
        session_state[DRAFT_RECOMMENDATION_OUTPUT_KEY] = json.dumps(
            {
                "decision": "trade",
                "stop_loss": 88.0,
                "memo": "Corrupted stop_loss 88.0",
                "memo_claims": ["Stop loss is 88.0"],
            }
        )
        return orig_run_bounded(**kwargs)

    monkeypatch.setattr(AgenticAnalysisWorkflow, "_review_critique", _revise_critic)
    monkeypatch.setattr(blackboard_module, "run_bounded_critic_revision", _corrupting_revision)

    response = workflow.analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=AS_OF))

    assert len(critic_calls) == 1
    assert response.decision == "trade"
    for field_name in RISK_FIELDS:
        assert getattr(response, field_name) == getattr(baseline, field_name)
    assert "critic_revision_failed" in response.warnings
    assert response.memo is not None
    assert response.memo.thesis == "Initial thesis."
    assert response.critic_effect is not None
    assert response.critic_effect.revisions_performed == 0
    assert "guardrail_rejected: risk_number_mismatch" in response.critic_effect.reason_codes


def test_post_revision_gate_rejects_legacy_revision_risk_mutation(monkeypatch):
    baseline = _baseline(decision="trade", with_risk=True)
    workflow, _ = _setup_blackboard_workflow(
        monkeypatch,
        baseline=baseline,
        synthesizer_contract="legacy",
    )

    def _legacy_runner(_registry, _ticker):
        return iter(
            [
                SimpleNamespace(
                    author="fundamental_analyst",
                    content=SimpleNamespace(parts=[SimpleNamespace(text="ok")]),
                    usage_metadata=None,
                    error_code=None,
                    error_message=None,
                ),
                SimpleNamespace(
                    author="technical_analyst",
                    content=SimpleNamespace(parts=[SimpleNamespace(text="ok")]),
                    usage_metadata=None,
                    error_code=None,
                    error_message=None,
                ),
                SimpleNamespace(
                    author="decision_synthesizer",
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(text='{"decision":"trade","confidence":0.7,"reasons":[]}')]
                    ),
                    usage_metadata=None,
                    error_code=None,
                    error_message=None,
                ),
                SimpleNamespace(
                    author="critic_guardrail",
                    content=SimpleNamespace(parts=[SimpleNamespace(text="ok")]),
                    usage_metadata=None,
                    error_code=None,
                    error_message=None,
                ),
            ]
        )

    workflow.runner_factory = _legacy_runner
    critic_calls = []

    def _revise_critic(self, critique_request):
        del self
        critic_calls.append(critique_request)
        return CritiqueResult(
            response=CritiqueResponse(
                run_id=critique_request.run_id,
                context_hash=critique_request.context_hash,
                status="completed",
                verdict="revise",
                objections=[
                    CritiqueObjection(
                        objection_id="model:1",
                        category="unsupported_claim",
                        severity="low",
                        source="model",
                        claim="Clarify stop.",
                    )
                ],
                revision_instructions="Clarify stop.",
                summary="Revise.",
            ),
            validation=CritiqueValidation(accepted=True),
            model_name="offline-critic",
            prompt_version="critic-guardrail.v1",
        )

    def _mutating_legacy_revision(self, **kwargs):
        del self, kwargs
        return _DecisionRevisionResult(
            synthesis=DecisionSynthesis(
                decision="trade",
                confidence=0.70,
                reasons=["Mutated stop loss"],
                stop_loss=80.0,
            ),
            raw_output='{"decision":"trade","confidence":0.7,"stop_loss":80.0}',
            token_usage={"total_token_count": 3},
            latency_ms=1.0,
        )

    monkeypatch.setattr(AgenticAnalysisWorkflow, "_review_critique", _revise_critic)
    monkeypatch.setattr(AgenticAnalysisWorkflow, "_request_synthesis_revision", _mutating_legacy_revision)

    response = workflow.analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=AS_OF))

    assert len(critic_calls) == 1
    assert response.stop_loss == 95.0
    assert "critic_revision_failed" in response.warnings
    assert response.critic_effect is not None
    assert response.critic_effect.revisions_performed == 0
    assert "guardrail_rejected: risk_number_mismatch" in response.critic_effect.reason_codes


@pytest.mark.parametrize("critic_mode", ["off", "enforced"])
def test_degraded_error_critic_guardrail_event_normalizes_adk_token_usage(monkeypatch, critic_mode):
    _poison_network(monkeypatch)
    settings = Settings(
        _env_file=None,
        PIPELINE_MODE="agentic",
        FUNDAMENTAL_AGENT_MODE="off",
        TECHNICAL_AGENT_MODE="off",
        SYNTHESIZER_CONTRACT="legacy",
        CRITIC_MODE=critic_mode,
    )
    monkeypatch.setattr(workflow_module, "get_settings", lambda: settings)
    baseline = _baseline(decision="watchlist")
    monkeypatch.setattr(
        workflow_module,
        "resolve_ticker",
        lambda ref: SimpleNamespace(is_valid=True, ticker="NVDA", error=None),
    )
    monkeypatch.setattr(workflow_module, "analyze_request", lambda req, engine=None: baseline.model_copy(deep=True))
    monkeypatch.setattr(workflow_module.AgenticAnalysisWorkflow, "_persist_trace", lambda self, trace: None)

    def _runner(_registry, _ticker):
        return iter(
            [
                SimpleNamespace(
                    author="fundamental_analyst",
                    content=SimpleNamespace(parts=[SimpleNamespace(text="ok")]),
                    usage_metadata=None,
                    error_code=None,
                    error_message=None,
                ),
                SimpleNamespace(
                    author="technical_analyst",
                    content=SimpleNamespace(parts=[SimpleNamespace(text="ok")]),
                    usage_metadata=None,
                    error_code=None,
                    error_message=None,
                ),
                SimpleNamespace(
                    author="decision_synthesizer",
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(text='{"decision":"watchlist","confidence":0.6,"reasons":[]}')]
                    ),
                    usage_metadata=None,
                    error_code=None,
                    error_message=None,
                ),
                SimpleNamespace(
                    author="critic_guardrail",
                    content=SimpleNamespace(parts=[SimpleNamespace(text="error narration")]),
                    usage_metadata=SimpleNamespace(prompt_token_count=4, total_token_count=9),
                    error_code="500",
                    error_message="boom",
                ),
            ]
        )

    workflow = AgenticAnalysisWorkflow(
        load_agent_config(settings),
        runner_factory=_runner,
        llm_io_recorder=_NoopRecorder(),
    )

    response = workflow.analyze("NVDA", request=AnalyzeRequest(ticker="NVDA", as_of_date=AS_OF))
    critic_step = next(step for step in response.trace.steps if step.agent_name == "critic_guardrail")
    assert critic_step.status == "degraded"
    assert critic_step.output is not None
    assert critic_step.output["adk_token_usage"] == {"prompt_token_count": 4, "total_token_count": 9}
    violations = evaluate_agentic_invariants(
        deterministic_response=baseline,
        agentic_response=response,
        trace=response.trace,
        allowed_skip_reasons=CRITIC_OFF_ALLOWED_SKIPS,
    )
    assert "critic_redundant_adk_tokens" in {v.code for v in violations}
