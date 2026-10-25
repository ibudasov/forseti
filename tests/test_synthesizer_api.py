"""API tests for the typed Decision Synthesizer memo and its persisted effect."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import agents.orchestration.workflow as workflow_module
from agents.config import load_agent_config
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.main import app, get_analysis_engine, get_pipeline_override_allowed
from app.schemas.analyze import AnalyzeResponse
from app.services.decision_synthesizer import DecisionSynthesizer, ModelOutput
from app.settings import Settings


class _ScriptedModel:
    model_name = "scripted-synthesizer"

    def generate(self, prompt: str) -> ModelOutput:
        request = json.loads(prompt.split("DecisionSynthesisRequest:\n", maxsplit=1)[1])
        memo = {
            "schema_version": "1.0",
            "run_id": request["run_id"],
            "context_hash": request["context_hash"],
            "proposed_decision": "watchlist",
            "confidence": 0.5,
            "thesis": "Price data is stale.",
            "key_reasons": [{"claim": "Prices are stale.", "warning_codes": ["stale_price_data"]}],
            "key_risks": [],
            "what_would_change": [],
        }
        return ModelOutput(text=json.dumps(memo), token_usage={})


def _event(author: str, text: str = ""):
    return SimpleNamespace(
        author=author,
        content=SimpleNamespace(parts=[SimpleNamespace(text=text)] if text else []),
        usage_metadata=SimpleNamespace(),
        error_code=None,
        error_message=None,
    )


@pytest.fixture
def agentic_api(db_engine, monkeypatch):
    app.dependency_overrides[get_analysis_engine] = lambda: db_engine
    app.dependency_overrides[get_pipeline_override_allowed] = lambda: True
    monkeypatch.setattr(workflow_module, "_critic_mode", lambda: "off")
    monkeypatch.setattr(workflow_module, "_fundamental_agent_mode", lambda: "off")
    monkeypatch.setattr(workflow_module, "_technical_agent_mode", lambda: "off")
    monkeypatch.setattr(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: AnalyzeResponse(
            ticker="NVDA",
            decision="watchlist",
            confidence=0.64,
            reasons=["deterministic"],
            warnings=["stale_price_data"],
            engine_version="v1.rules.0",
            trace_id="deterministic-trace",
        ),
    )

    def _run_workflow(_pipeline, request):
        workflow = AgenticAnalysisWorkflow(
            load_agent_config(Settings(_env_file=None)),
            engine=db_engine,
            runner_factory=lambda registry, ticker: iter(
                [_event("decision_synthesizer", "typed"), _event("critic_guardrail", "clear")]
            ),
            decision_synthesizer=DecisionSynthesizer(_ScriptedModel(), max_retries=0),
        )
        return workflow.analyze(request.ticker, request=request)

    monkeypatch.setattr("app.services.pipeline.AgenticPipeline.analyze", _run_workflow)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_analyze_returns_cited_memo_in_typed_mode(agentic_api, monkeypatch):
    monkeypatch.setattr(workflow_module, "_synthesizer_contract", lambda: "typed")

    body = agentic_api.post("/analyze?pipeline=agentic", json={"ticker": "NVDA"}).json()

    assert body["memo"]["thesis"] == "Price data is stale."
    assert body["memo"]["key_reasons"] == [
        {"claim": "Prices are stale.", "metric_ids": [], "chunk_ids": [], "warning_codes": ["stale_price_data"]}
    ]
    assert body["memo"]["risk_block"] == "Deterministic risk: no trade levels available."


def test_analyze_memo_is_null_in_legacy_mode(agentic_api, monkeypatch):
    monkeypatch.setattr(workflow_module, "_synthesizer_contract", lambda: "legacy")

    body = agentic_api.post("/analyze?pipeline=agentic", json={"ticker": "NVDA"}).json()

    assert "memo" in body
    assert body["memo"] is None


def test_persisted_synthesizer_effect_is_queryable_by_run_id(agentic_api, monkeypatch):
    monkeypatch.setattr(workflow_module, "_synthesizer_contract", lambda: "typed")
    run_id = agentic_api.post("/analyze?pipeline=agentic&include_trace=true", json={"ticker": "NVDA"}).json()["trace"][
        "run_id"
    ]

    effect = agentic_api.get(f"/runs/{run_id}").json()["synthesizer_effect"]

    assert effect["contract"] == "typed"
    assert effect["fallback_used"] is False
    assert (effect["proposed_decision"], effect["final_decision"]) == ("watchlist", "watchlist")
    assert len(effect["memo_hash"]) == 64


def test_openapi_documents_the_memo_field():
    schema = TestClient(app).get("/openapi.json").json()["components"]["schemas"]

    assert "memo" in schema["AnalyzeResponse"]["properties"]
    assert set(schema["DecisionMemo"]["properties"]) == {
        "thesis",
        "key_reasons",
        "key_risks",
        "what_would_change",
        "risk_block",
    }
