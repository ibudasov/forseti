import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

from google.adk.agents import ParallelAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents.adk_app.agent import build_adk_root_agent, root_agent
from agents.config import load_agent_config
from agents.orchestration.blackboard import (
    ANALYSIS_RESPONSE_KEY,
    CRITIC_EFFECT_KEY,
    CRITIQUE_OUTPUT_KEY,
    CRITIQUE_REQUEST_KEY,
    CRITIQUE_RESULT_KEY,
    CROSS_DOMAIN_TENSION_KEY,
    DECISION_MEMO_RESULT_KEY,
    DECISION_SYNTHESIS_REQUEST_KEY,
    DRAFT_RECOMMENDATION_OUTPUT_KEY,
    FUNDAMENTAL_ASSESSMENT_KEY,
    FUNDAMENTAL_EFFECT_KEY,
    FUNDAMENTAL_REQUEST_KEY,
    FUNDAMENTAL_VIEW_OUTPUT_KEY,
    SYNTHESIZER_EFFECT_KEY,
    TECHNICAL_ASSESSMENT_KEY,
    TECHNICAL_EFFECT_KEY,
    TECHNICAL_REQUEST_KEY,
    TECHNICAL_VIEW_OUTPUT_KEY,
    BlackboardRuntime,
)
from agents.orchestration.registry import SPECIALIST_FAN_OUT_NAME, SPECIALIST_ORDER, build_agent_registry
from app.domain.fundamentals import DeterministicFundamentalAnalysis
from app.domain.technicals import DeterministicTechnicalAnalysis
from app.schemas.analyze import AnalyzeResponse, DecisionDiagnosis
from app.schemas.critique import CritiqueResponse, CritiqueResult, CritiqueValidation
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
    TechnicalFinding,
)
from app.services.critique_context import build_critique_request
from app.services.cross_domain_tension import CrossDomainTensionResolution
from app.services.decision_synthesizer import DecisionSynthesizer, ModelOutput
from app.settings import Settings


def test_adk_app_exposes_trade_supervisor():
    expected_root_agent = build_agent_registry(load_agent_config(Settings(_env_file=None))).root_agent

    assert expected_root_agent is not None
    assert root_agent is not None
    assert root_agent.name == expected_root_agent.name
    assert [agent.name for agent in root_agent.sub_agents] == list(SPECIALIST_ORDER)


def test_build_adk_root_agent_runs_typed_blackboard_spine_on_session_state(monkeypatch):
    import socket

    def _reject_network(*_args, **_kwargs):
        raise AssertionError("Live network calls are forbidden.")

    monkeypatch.setattr(socket.socket, "connect", _reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _reject_network)

    as_of = date(2026, 9, 8)
    snapshot = datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc)
    baseline = AnalyzeResponse(
        ticker="NVDA",
        decision="trade",
        confidence=0.8,
        reasons=["Strong deterministic checklist"],
        warnings=[],
        entry_range=(100.0, 102.0),
        stop_loss=95.0,
        take_profit=(112.0, 118.0),
        risk_reward=2.5,
        position_size_eur=1000.0,
        engine_version="v1.rules.0",
        trace_id="baseline",
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="checklist_passed",
            detail="score 8/11",
            checklist_score=8,
            debug_reason="checklist/checklist_passed: score 8/11",
        ),
    )
    fund_req = FundamentalAnalysisRequest(
        run_id="run-adk",
        context_hash="fund-hash",
        ticker="NVDA",
        company_name="NVIDIA",
        sector="ai",
        currency="USD",
        snapshot_at=snapshot,
        as_of_date=as_of,
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
                text="Demand strong.",
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
            selected_character_count=14,
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
    fund_res = FundamentalAssessmentResult(
        response=FundamentalAssessmentResponse(
            run_id="run-adk",
            context_hash="fund-hash",
            status="completed",
            overall_signal="positive",
            proposed_score_adjustment=0,
            findings=[
                CitedFinding(
                    finding_id="f1",
                    category="growth_quality",
                    direction="positive",
                    materiality="medium",
                    claim="Demand strong.",
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
    tech_req = TechnicalAnalysisRequest(
        run_id="run-adk",
        context_hash="tech-hash",
        ticker="NVDA",
        sector="ai",
        snapshot_at=snapshot,
        as_of_date=as_of,
        deterministic_result=DeterministicTechnicalAnalysis(
            ticker="NVDA",
            as_of_date=as_of,
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
            latest_bar_date=as_of,
            bars_stale=False,
            series_truncated=False,
        ),
    )
    tech_res = TechnicalAssessmentResult(
        response=TechnicalAssessmentResponse(
            run_id="run-adk",
            context_hash="tech-hash",
            status="completed",
            overall_signal="positive",
            trend_state="uptrend",
            momentum_state="strengthening",
            proposed_score_adjustment=0,
            findings=[
                TechnicalFinding(
                    finding_id="t1",
                    category="trend",
                    direction="positive",
                    materiality="low",
                    claim="Uptrend intact.",
                    metric_ids=[],
                )
            ],
            summary="Positive technicals.",
        ),
        validation=TechnicalAssessmentValidation(accepted=True),
        model_name="offline-tech",
        prompt_version="v1",
    )

    class _ScriptedSynthModel:
        model_name = "offline-synth"

        def generate(self, prompt: str) -> ModelOutput:
            payload = json.loads(prompt.split("DecisionSynthesisRequest:\n", 1)[1])
            return ModelOutput(
                text=json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": payload["run_id"],
                        "context_hash": payload["context_hash"],
                        "proposed_decision": "trade",
                        "confidence": 0.78,
                        "thesis": "Both analysts support trade.",
                        "key_reasons": [],
                        "key_risks": [],
                        "what_would_change": [],
                    }
                ),
                token_usage={"total_token_count": 5},
            )

    runtime = BlackboardRuntime(
        fundamental_mode="enforced",
        embedding_client_provider=lambda: None,
        technical_mode="enforced",
        synthesizer_contract="typed",
        critic_mode="enforced",
        analyze_request_fn=lambda req, engine=None: baseline.model_copy(deep=True),
        build_fundamental_request_fn=lambda *a, **k: fund_req,
        fundamental_analyst_factory=lambda: SimpleNamespace(assess=lambda req: fund_res),
        build_technical_request_fn=lambda *a, **k: tech_req,
        technical_analyst_factory=lambda: SimpleNamespace(assess=lambda req: tech_res),
        resolve_tension_fn=lambda **k: CrossDomainTensionResolution(tension=None, supplemental_chunks=[]),
        decision_synthesizer_provider=lambda: DecisionSynthesizer(_ScriptedSynthModel(), max_retries=0),
        build_critique_request_fn=lambda *a, **k: build_critique_request(
            *a,
            **{key: value for key, value in k.items() if key != "fundamental_request"},
            fundamental_request=fund_req,
        ),
        evaluate_deterministic_objections_fn=lambda **k: [],
        review_critique_fn=lambda req: CritiqueResult(
            response=CritiqueResponse(
                run_id=req.run_id,
                context_hash=req.context_hash,
                status="completed",
                verdict="accept",
                summary="Clean draft.",
            ),
            validation=CritiqueValidation(accepted=True),
            model_name="offline-critic",
            prompt_version="critic-guardrail.v1",
        ),
    )
    config = load_agent_config(
        Settings(
            _env_file=None,
            PIPELINE_MODE="agentic",
            FUNDAMENTAL_AGENT_MODE="enforced",
            TECHNICAL_AGENT_MODE="enforced",
            SYNTHESIZER_CONTRACT="typed",
            CRITIC_MODE="enforced",
        )
    )
    adk_root = build_adk_root_agent(config, blackboard_runtime=runtime)
    assert adk_root is not None
    assert isinstance(adk_root.sub_agents[0], ParallelAgent)
    assert adk_root.sub_agents[0].name == SPECIALIST_FAN_OUT_NAME

    session_service = InMemorySessionService()
    session = asyncio.run(session_service.create_session(app_name="forseti", user_id="NVDA"))
    runner = Runner(app_name="forseti", agent=adk_root, session_service=session_service)
    list(
        runner.run(
            user_id="NVDA",
            session_id=session.id,
            new_message=Content(role="user", parts=[Part(text="Analyze ticker NVDA")]),
        )
    )
    final_session = asyncio.run(session_service.get_session(app_name="forseti", user_id="NVDA", session_id=session.id))
    assert final_session is not None
    state = final_session.state
    for key in (
        FUNDAMENTAL_VIEW_OUTPUT_KEY,
        FUNDAMENTAL_REQUEST_KEY,
        FUNDAMENTAL_ASSESSMENT_KEY,
        FUNDAMENTAL_EFFECT_KEY,
        TECHNICAL_VIEW_OUTPUT_KEY,
        TECHNICAL_REQUEST_KEY,
        TECHNICAL_ASSESSMENT_KEY,
        TECHNICAL_EFFECT_KEY,
        CROSS_DOMAIN_TENSION_KEY,
        DECISION_SYNTHESIS_REQUEST_KEY,
        DECISION_MEMO_RESULT_KEY,
        SYNTHESIZER_EFFECT_KEY,
        DRAFT_RECOMMENDATION_OUTPUT_KEY,
        CRITIQUE_REQUEST_KEY,
        CRITIQUE_RESULT_KEY,
        CRITIC_EFFECT_KEY,
        CRITIQUE_OUTPUT_KEY,
        ANALYSIS_RESPONSE_KEY,
    ):
        assert key in state
    assert state[ANALYSIS_RESPONSE_KEY]["decision"] == "trade"
    assert Decimal(str(state[ANALYSIS_RESPONSE_KEY]["stop_loss"])) == Decimal("95.0")
