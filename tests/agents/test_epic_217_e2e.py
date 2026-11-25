"""Combined offline E2E tests for Epic #217 (#213 + #214 + #215 + #216)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import socket
import threading
import time
from types import SimpleNamespace
from typing import Any

from sqlmodel import Session, select

import agents.orchestration.workflow as workflow_module
import app.services.fundamental_context as fundamental_context_module
from agents.config import load_agent_config
from agents.orchestration.constants import CRITIC_NAME, SPECIALIST_ORDER
from agents.orchestration.workflow import MAX_CRITIC_REVISIONS, AgenticAnalysisWorkflow
from app.db.models import DocumentChunk, SourceQualityTier, SourceType
from app.db.repository import upsert_document_chunks
from app.rag.ingestion.base import compute_source_hash
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
    FundamentalAssessmentResponse,
    FundamentalAssessmentResult,
    FundamentalAssessmentValidation,
)
from app.schemas.technicals import (
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
    TechnicalAssessmentValidation,
    TechnicalFinding,
)
from app.services.decision_synthesizer import DecisionSynthesizer, ModelOutput
from app.settings import Settings
from app.trust.invariants import (
    CRITIC_OFF_ALLOWED_SKIPS,
    RISK_FIELDS,
    evaluate_agentic_invariants,
)
from tests.agents.test_cross_domain_tension_workflow import DeterministicEmbeddingClient
from tests.agents.test_parallel_specialists import _seed_full_security

AS_OF_DATE = date(2026, 9, 8)
SNAPSHOT_AT = datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc)


def _guard_offline_network(monkeypatch, db_engine) -> None:
    allowed_host = db_engine.url.host or "postgresql"
    allowed_port = int(db_engine.url.port or 5432)
    allowed_ips = {allowed_host, "127.0.0.1", "::1"}
    try:
        for info in socket.getaddrinfo(allowed_host, allowed_port):
            allowed_ips.add(info[4][0])
    except OSError:
        pass
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _check(sock, address) -> None:
        if sock.family == socket.AF_UNIX:
            return
        host = address[0] if isinstance(address, tuple) and address else None
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if host in allowed_ips and port == allowed_port:
            return
        raise AssertionError(f"External network I/O is forbidden in offline E2E tests: {address!r}")

    def _guarded_connect(self, address):
        _check(self, address)
        return real_connect(self, address)

    def _guarded_connect_ex(self, address):
        _check(self, address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)


def _configure_settings(
    monkeypatch,
    *,
    fundamental_mode: str = "enforced",
    technical_mode: str = "enforced",
    critic_mode: str = "enforced",
    synthesizer_contract: str = "typed",
) -> Settings:
    settings = Settings(
        _env_file=None,
        PIPELINE_MODE="agentic",
        FUNDAMENTAL_AGENT_MODE=fundamental_mode,
        TECHNICAL_AGENT_MODE=technical_mode,
        CRITIC_MODE=critic_mode,
        SYNTHESIZER_CONTRACT=synthesizer_contract,
        DEBUG_LLM_IO=False,
        VERTEX_AI_PROJECT=None,
        GOOGLE_CLOUD_PROJECT=None,
        AGENT_MAX_RETRIES=1,
    )
    monkeypatch.setattr(workflow_module, "get_settings", lambda: settings)
    monkeypatch.setattr("app.settings.get_settings", lambda: settings)
    monkeypatch.setattr("agents.config.get_settings", lambda: settings)
    return settings


def _trade_baseline(ticker: str) -> AnalyzeResponse:
    return AnalyzeResponse(
        ticker=ticker,
        decision="trade",
        confidence=0.80,
        reasons=["Deterministic checklist passed"],
        warnings=[],
        entry_range=(100.0, 102.0),
        stop_loss=95.0,
        take_profit=(112.0, 118.0),
        risk_reward=2.5,
        position_size_eur=1000.0,
        engine_version="v1.rules.0",
        trace_id="det-trace-e2e",
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="checklist_passed",
            detail="score 8/11",
            checklist_score=8,
            debug_reason="checklist/checklist_passed: score 8/11",
        ),
    )


def _seed_pit_chunks(db_engine, *, ticker: str, embedder: DeterministicEmbeddingClient) -> tuple[int, int, int]:
    past_pub = SNAPSHOT_AT - timedelta(days=10)
    tension_pub = SNAPSHOT_AT - timedelta(days=5)
    future_pub = SNAPSHOT_AT + timedelta(days=3)
    texts = [
        f"{ticker} quarterly revenue growth and operating cash flow expansion.",
        f"{ticker} risk catalyst guidance margin compression and breakdown overhang.",
        f"{ticker} future breaking news after snapshot cutoff that must never leak.",
    ]
    vectors = embedder.embed_texts(texts)
    chunks = [
        DocumentChunk(
            ticker=ticker,
            document_id=f"{ticker}-doc-past",
            source_type=SourceType.filing_mda,
            source_url=f"https://example.com/{ticker}/past",
            publisher="SEC",
            title=f"{ticker} 10-Q MD&A",
            source_hash=compute_source_hash(f"https://example.com/{ticker}/past", 0, texts[0]),
            chunk_index=0,
            text=texts[0],
            published_at=past_pub,
            source_quality_tier=SourceQualityTier.primary_regulatory,
            embedding=vectors[0],
        ),
        DocumentChunk(
            ticker=ticker,
            document_id=f"{ticker}-doc-tension",
            source_type=SourceType.earnings_release,
            source_url=f"https://example.com/{ticker}/tension",
            publisher="IR",
            title=f"{ticker} Earnings Release",
            source_hash=compute_source_hash(f"https://example.com/{ticker}/tension", 0, texts[1]),
            chunk_index=0,
            text=texts[1],
            published_at=tension_pub,
            source_quality_tier=SourceQualityTier.primary_company,
            embedding=vectors[1],
        ),
        DocumentChunk(
            ticker=ticker,
            document_id=f"{ticker}-doc-future",
            source_type=SourceType.earnings_release,
            source_url=f"https://example.com/{ticker}/future",
            publisher="Wire",
            title=f"{ticker} Post-Cutoff Update",
            source_hash=compute_source_hash(f"https://example.com/{ticker}/future", 0, texts[2]),
            chunk_index=0,
            text=texts[2],
            published_at=future_pub,
            source_quality_tier=SourceQualityTier.secondary_reputable,
            embedding=vectors[2],
        ),
    ]
    upsert_document_chunks(chunks, engine=db_engine)
    with Session(db_engine) as session:
        rows = session.exec(select(DocumentChunk).where(DocumentChunk.ticker == ticker)).all()
    by_url = {row.source_url: int(row.id or 0) for row in rows}
    return (
        by_url[f"https://example.com/{ticker}/past"],
        by_url[f"https://example.com/{ticker}/tension"],
        by_url[f"https://example.com/{ticker}/future"],
    )


def _positive_fundamental(req) -> FundamentalAssessmentResult:
    chunk_ids = [req.evidence_chunks[0].chunk_id] if req.evidence_chunks else []
    metric_ids = [req.deterministic_result.metrics[0].metric_id] if req.deterministic_result.metrics else []
    return FundamentalAssessmentResult(
        response=FundamentalAssessmentResponse(
            run_id=req.run_id,
            context_hash=req.context_hash,
            status="completed",
            overall_signal="positive",
            proposed_score_adjustment=0,
            findings=[
                CitedFinding(
                    finding_id="f-e2e-1",
                    category="growth_quality",
                    direction="positive",
                    materiality="medium",
                    claim="Revenue growth and cash flow remain solid.",
                    metric_ids=metric_ids,
                    chunk_ids=chunk_ids,
                )
            ],
            evidence_coverage=1.0,
            summary="Positive fundamentals.",
        ),
        validation=FundamentalAssessmentValidation(accepted=True),
        model_name="offline-fund-e2e",
        prompt_version="fundamental-analyst.v2",
        token_usage={"total_token_count": 12},
    )


def _conflicting_technical(req) -> TechnicalAssessmentResult:
    metric_ids = [req.facts[0].metric_id] if req.facts else ["price_close"]
    return TechnicalAssessmentResult(
        response=TechnicalAssessmentResponse(
            run_id=req.run_id,
            context_hash=req.context_hash,
            status="completed",
            overall_signal="negative",
            trend_state="downtrend",
            momentum_state="weakening",
            proposed_score_adjustment=0,
            findings=[
                TechnicalFinding(
                    finding_id="t-e2e-1",
                    category="trend",
                    direction="negative",
                    materiality="high",
                    claim="Price broke below trend support.",
                    metric_ids=metric_ids,
                )
            ],
            summary="Technical breakdown conflicts with fundamentals.",
        ),
        validation=TechnicalAssessmentValidation(accepted=True),
        model_name="offline-tech-e2e",
        prompt_version="technical-analyst.v1",
        token_usage={"total_token_count": 11},
    )


def _synth_payload(payload: dict[str, Any], *, decision: str, confidence: float, thesis: str) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "run_id": payload["run_id"],
            "context_hash": payload["context_hash"],
            "proposed_decision": decision,
            "confidence": confidence,
            "thesis": thesis,
            "key_reasons": [],
            "key_risks": [],
            "what_would_change": [],
        }
    )


def test_epic_217_full_pipeline_parallel_tension_revision_and_single_critic_call(
    db_engine,
    monkeypatch,
) -> None:
    """Combined E2E for #213 + #214 + #215 + #216 on real ADK Runner + pgvector."""
    _guard_offline_network(monkeypatch, db_engine)
    ticker = "EP217"
    _seed_full_security(db_engine, ticker=ticker, as_of_date=AS_OF_DATE)
    embedder = DeterministicEmbeddingClient()
    _past_id, _tension_id, future_id = _seed_pit_chunks(db_engine, ticker=ticker, embedder=embedder)
    settings = _configure_settings(monkeypatch, critic_mode="enforced", synthesizer_contract="typed")
    monkeypatch.setattr(fundamental_context_module, "_build_embedding_client", lambda: embedder)

    baseline = _trade_baseline(ticker)
    monkeypatch.setattr(
        workflow_module,
        "resolve_ticker",
        lambda ref: SimpleNamespace(is_valid=True, ticker=ticker, error=None),
    )
    monkeypatch.setattr(workflow_module, "analyze_request", lambda req, engine=None: baseline.model_copy(deep=True))

    barrier = threading.Barrier(2, timeout=5.0)
    intervals: dict[str, tuple[float, float]] = {}

    def _fund_assess(req):
        start = time.monotonic()
        barrier.wait()
        result = _positive_fundamental(req)
        intervals["fundamental"] = (start, time.monotonic())
        return result

    def _tech_assess(req):
        start = time.monotonic()
        barrier.wait()
        result = _conflicting_technical(req)
        intervals["technical"] = (start, time.monotonic())
        return result

    monkeypatch.setattr(workflow_module, "FundamentalAnalyst", lambda: SimpleNamespace(assess=_fund_assess))
    monkeypatch.setattr(workflow_module, "TechnicalAnalyst", lambda: SimpleNamespace(assess=_tech_assess))

    synth_payloads: list[dict[str, Any]] = []

    class _ScriptedSynth:
        model_name = "e2e-synth"

        def generate(self, prompt: str) -> ModelOutput:
            payload = json.loads(prompt.split("DecisionSynthesisRequest:\n", 1)[1])
            synth_payloads.append(payload)
            if len(synth_payloads) == 1:
                text = _synth_payload(payload, decision="trade", confidence=0.78, thesis="Initial memo.")
            else:
                text = _synth_payload(payload, decision="watchlist", confidence=0.60, thesis="Revised tension memo.")
            return ModelOutput(text=text, token_usage={"total_token_count": 10})

    critic_calls = 0

    def _review_critique(critique_request) -> CritiqueResult:
        nonlocal critic_calls
        critic_calls += 1
        assert critique_request.cross_domain_tension is not None
        assert future_id not in {chunk.chunk_id for chunk in critique_request.evidence_chunks}
        metric_ids = [
            finding.metric_ids[0]
            for view in critique_request.analyst_views
            for finding in view.findings
            if finding.metric_ids
        ][:1]
        objection = CritiqueObjection(
            objection_id="obj-tension-1",
            category="analyst_contradiction",
            severity="medium",
            source="model",
            claim="Address fundamental vs technical tension.",
            metric_ids=metric_ids or ["price_close"],
        )
        return CritiqueResult(
            response=CritiqueResponse(
                run_id=critique_request.run_id,
                context_hash=critique_request.context_hash,
                status="completed",
                verdict="revise",
                objections=[objection],
                revision_instructions="Clarify cross-domain tension and soften decision.",
                revision_objection_ids=["obj-tension-1"],
                revision_targets=[
                    CritiqueRevisionTarget(
                        objection_id="obj-tension-1",
                        target="thesis",
                        action="clarify_tension",
                    )
                ],
                summary="One bounded revision requested.",
            ),
            validation=CritiqueValidation(accepted=True),
            model_name="e2e-critic",
            prompt_version="critic.v1",
            token_usage={"total_token_count": 9},
        )

    workflow = AgenticAnalysisWorkflow(
        load_agent_config(settings),
        engine=db_engine,
        embedding_client=embedder,
        decision_synthesizer=DecisionSynthesizer(model_port=_ScriptedSynth(), max_retries=0),
    )
    monkeypatch.setattr(workflow, "_review_critique", _review_critique)

    response = workflow.analyze(ticker, request=AnalyzeRequest(ticker=ticker, as_of_date=AS_OF_DATE))
    trace = response.trace
    assert trace is not None

    # #214: Both specialists overlapped via Barrier and trace preserves SPECIALIST_ORDER
    assert "fundamental" in intervals and "technical" in intervals
    specialist_steps = [step.agent_name for step in trace.steps if step.agent_name in SPECIALIST_ORDER]
    for name in SPECIALIST_ORDER:
        assert name in specialist_steps

    # #215: CrossDomainTension reached synthesis and zero future chunks leaked
    assert len(synth_payloads) == 1 + MAX_CRITIC_REVISIONS
    assert synth_payloads[0]["cross_domain_tension"] is not None
    assert future_id not in synth_payloads[0]["known_chunk_ids"]

    # #216: Structured objection IDs routed to revision synthesizer
    assert "obj-tension-1" in synth_payloads[1]["revision_objection_ids"]
    assert synth_payloads[1]["revision_targets"][0]["objection_id"] == "obj-tension-1"

    # #213: Exactly 1 Critic LLM call total (no duplicate ADK call, no 2nd call after revision)
    assert critic_calls == 1
    assert response.decision == "watchlist"
    assert response.confidence <= baseline.confidence
    for field_name in RISK_FIELDS:
        assert getattr(response, field_name) == getattr(baseline, field_name)
    assert evaluate_agentic_invariants(
        deterministic_response=baseline,
        agentic_response=response,
        trace=trace,
    ) == []


def test_epic_217_critic_off_vs_shadow_vs_enforced_parity(db_engine, monkeypatch) -> None:
    """Verify CRITIC_MODE=off (0 calls, skipped) vs shadow/enforced (1 call) with zero invariant violations."""
    _guard_offline_network(monkeypatch, db_engine)
    ticker = "CP217"
    _seed_full_security(db_engine, ticker=ticker, as_of_date=AS_OF_DATE)
    embedder = DeterministicEmbeddingClient()
    _seed_pit_chunks(db_engine, ticker=ticker, embedder=embedder)
    monkeypatch.setattr(fundamental_context_module, "_build_embedding_client", lambda: embedder)

    baseline = _trade_baseline(ticker)
    monkeypatch.setattr(
        workflow_module,
        "resolve_ticker",
        lambda ref: SimpleNamespace(is_valid=True, ticker=ticker, error=None),
    )
    monkeypatch.setattr(workflow_module, "analyze_request", lambda req, engine=None: baseline.model_copy(deep=True))
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(assess=_positive_fundamental),
    )
    monkeypatch.setattr(
        workflow_module,
        "TechnicalAnalyst",
        lambda: SimpleNamespace(assess=_conflicting_technical),
    )

    class _StableSynth:
        model_name = "e2e-synth"

        def generate(self, prompt: str) -> ModelOutput:
            payload = json.loads(prompt.split("DecisionSynthesisRequest:\n", 1)[1])
            return ModelOutput(
                text=_synth_payload(payload, decision="trade", confidence=0.78, thesis="Steady memo."),
                token_usage={"total_token_count": 7},
            )

    outcomes: dict[str, tuple[AnalyzeResponse, int]] = {}
    for mode in ("off", "shadow", "enforced"):
        settings = _configure_settings(monkeypatch, critic_mode=mode, synthesizer_contract="typed")
        calls = 0

        def _review(critique_request) -> CritiqueResult:
            nonlocal calls
            calls += 1
            return CritiqueResult(
                response=CritiqueResponse(
                    run_id=critique_request.run_id,
                    context_hash=critique_request.context_hash,
                    status="completed",
                    verdict="reject",
                    proposed_decision="watchlist",
                    proposed_confidence_penalty=0.15,
                    objections=[
                        CritiqueObjection(
                            objection_id="obj-rej-1",
                            category="analyst_contradiction",
                            severity="high",
                            source="model",
                            claim="Conflicting specialist signals.",
                            metric_ids=["price_close"],
                        )
                    ],
                    summary="Downgrade to watchlist.",
                ),
                validation=CritiqueValidation(accepted=True),
                model_name="e2e-critic",
                prompt_version="critic.v1",
                token_usage={"total_token_count": 8},
            )

        wf = AgenticAnalysisWorkflow(
            load_agent_config(settings),
            engine=db_engine,
            embedding_client=embedder,
            decision_synthesizer=DecisionSynthesizer(model_port=_StableSynth(), max_retries=0),
        )
        monkeypatch.setattr(wf, "_review_critique", _review)
        resp = wf.analyze(ticker, request=AnalyzeRequest(ticker=ticker, as_of_date=AS_OF_DATE))
        trace = resp.trace
        assert trace is not None
        allowed_skips = CRITIC_OFF_ALLOWED_SKIPS if mode == "off" else None
        assert evaluate_agentic_invariants(
            deterministic_response=baseline,
            agentic_response=resp,
            trace=trace,
            allowed_skip_reasons=allowed_skips,
        ) == []
        if mode == "off":
            critic_steps = [step for step in trace.steps if step.agent_name == CRITIC_NAME]
            assert len(critic_steps) == 1
            assert critic_steps[0].status == "skipped"
            assert critic_steps[0].token_usage == {}
        outcomes[mode] = (resp, calls)

    assert outcomes["off"][1] == 0
    assert outcomes["shadow"][1] == 1
    assert outcomes["enforced"][1] == 1
    assert outcomes["off"][0].decision == "trade"
    assert outcomes["shadow"][0].decision == "trade"
    assert outcomes["enforced"][0].decision == "watchlist"
