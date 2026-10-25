from __future__ import annotations

import json
import sys
import types
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.critique import AnalystFinding, AnalystView
from app.schemas.synthesis import DecisionSynthesisRequest
from app.services.decision_synthesizer import (
    DecisionSynthesizer,
    ModelOutput,
    RetryableModelError,
    _response_schema,
    build_decision_synthesis_request,
    build_deterministic_fallback,
    render_decision_memo,
)


class _FakeModel:
    model_name = "fake-synthesizer"

    def __init__(self, outputs: list[str | Exception]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def generate(self, prompt: str) -> ModelOutput:
        assert "DecisionSynthesisRequest" in prompt
        self.calls += 1
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return ModelOutput(text=output, token_usage={"total_token_count": 5})


def test_valid_memo_is_accepted_and_risk_is_rendered_from_request():
    request = _request()
    model = _FakeModel([_response(request)])

    result = DecisionSynthesizer(model_port=model, max_retries=0).synthesize(request)

    assert result.validation.accepted is True
    assert result.used_fallback is False
    assert result.response.proposed_decision == "trade"
    assert "Entry: 10–11" in result.rendered_memo
    assert "Stop loss: 9" in result.rendered_memo
    assert "entry_range" not in result.response.model_dump(mode="json")


@pytest.mark.parametrize(
    ("change", "reason_code"),
    [
        ({"proposed_decision": "trade"}, "decision_promotion"),
        ({"confidence": 0.9}, "confidence_above_baseline"),
        (
            {"key_reasons": [{"claim": "RSI is 99.", "metric_ids": ["rsi:2026-09-08"]}]},
            "unsupported_numeric_claim",
        ),
        (
            {"key_reasons": [{"claim": "RSI is 60.", "metric_ids": ["unknown_metric"]}]},
            "unknown_metric_citation",
        ),
    ],
)
def test_invalid_memos_fall_back_to_deterministic_baseline(change, reason_code):
    request = _request(baseline_decision="watchlist")
    response = json.loads(_response(request))
    response.update(change)
    model = _FakeModel([json.dumps(response)])

    result = DecisionSynthesizer(model_port=model, max_retries=0).synthesize(request)

    assert result.used_fallback is True
    assert reason_code in result.validation.reason_codes
    assert result.response.proposed_decision == "watchlist"
    assert result.response.confidence == request.baseline_confidence


def test_money_field_leak_falls_back():
    request = _request()
    response = json.loads(_response(request))
    response["stop_loss"] = 9.0

    result = DecisionSynthesizer(
        model_port=_FakeModel([json.dumps(response)]),
        max_retries=0,
    ).synthesize(request)

    assert result.used_fallback is True
    assert result.validation.reason_codes == ["forbidden_response_field:stop_loss"]
    assert "stop_loss" not in result.response.model_dump(mode="json")


def test_schema_rejects_money_fields_and_request_promotions():
    request = _request()
    response = json.loads(_response(request))
    response["position_size_eur"] = 1000

    result = DecisionSynthesizer(
        model_port=_FakeModel([json.dumps(response)]),
        max_retries=0,
    ).synthesize(request)
    assert result.used_fallback is True
    assert result.validation.reason_codes == ["forbidden_response_field:position_size_eur"]

    with pytest.raises(ValidationError, match="must not include promotions"):
        build_decision_synthesis_request(
            **_request_values(
                baseline_decision="watchlist",
                allowed_decisions=["watchlist", "trade"],
            )
        )


def test_malformed_output_retries_then_accepts_valid_response():
    request = _request()
    model = _FakeModel(["not JSON", _response(request)])

    result = DecisionSynthesizer(model_port=model, max_retries=1).synthesize(request)

    assert model.calls == 2
    assert result.validation.accepted is True
    assert result.used_fallback is False


def test_provider_failure_uses_baseline_fallback():
    request = _request()
    model = _FakeModel([RetryableModelError("timeout")])

    result = DecisionSynthesizer(model_port=model, max_retries=0).synthesize(request)

    assert result.used_fallback is True
    assert result.validation.reason_codes == ["provider_error:timeout"]
    assert result.response.proposed_decision == request.baseline_decision
    assert result.response.confidence == request.baseline_confidence


def test_gemini_adapter_uses_inline_response_schema(monkeypatch):
    import app.services.decision_synthesizer as service_module

    request = _request()
    captured: dict = {}
    fake_vertexai = types.ModuleType("vertexai")
    fake_vertexai.init = lambda **kwargs: None
    fake_models = types.ModuleType("vertexai.generative_models")

    class _FakeGemini:
        def __init__(self, model_name):
            del model_name

        def generate_content(self, prompt, generation_config):
            del prompt
            captured.update(generation_config)
            return types.SimpleNamespace(text=_response(request), usage_metadata=None)

    class _FakeGenerationConfig:
        @classmethod
        def from_dict(cls, config):
            return config

    fake_models.GenerativeModel = _FakeGemini
    fake_models.GenerationConfig = _FakeGenerationConfig
    monkeypatch.setitem(sys.modules, "vertexai", fake_vertexai)
    monkeypatch.setitem(sys.modules, "vertexai.generative_models", fake_models)
    model = service_module.GeminiDecisionSynthesizerModel(
        model_name="fake-gemini",
        vertex_project="project",
        vertex_location="region",
        temperature=0.2,
    )

    model.generate("prompt")

    assert captured["response_mime_type"] == "application/json"
    assert captured["response_schema"] == _response_schema()
    assert "$defs" not in captured["response_schema"]
    assert "$ref" not in json.dumps(captured["response_schema"])
    assert "entry_range" not in captured["response_schema"]["properties"]


def test_fallback_and_rendered_risk_block_preserve_rule_outputs():
    request = _request()

    fallback = build_deterministic_fallback(request)
    memo = render_decision_memo(fallback, request)

    assert fallback.proposed_decision == "trade"
    assert fallback.confidence == request.baseline_confidence
    assert "Entry: 10–11" in memo
    assert "Position size (EUR): 250" in memo


def _request(
    *,
    baseline_decision: str = "trade",
    allowed_decisions: list[str] | None = None,
) -> DecisionSynthesisRequest:
    return build_decision_synthesis_request(
        **_request_values(
            baseline_decision=baseline_decision,
            allowed_decisions=allowed_decisions or (
                ["trade", "watchlist", "no_trade"] if baseline_decision == "trade" else ["watchlist", "no_trade"]
            ),
        )
    )


def _request_values(*, baseline_decision: str = "trade", allowed_decisions: list[str]) -> dict:
    finding = AnalystFinding(
        finding_id="technical-1",
        category="momentum",
        direction="positive",
        materiality="high",
        claim="RSI is 60 and momentum is stable.",
        metric_ids=["rsi:2026-09-08"],
        source_agent="technical_analyst",
    )
    return {
        "run_id": "run-1",
        "ticker": "NVDA",
        "as_of_date": date(2026, 9, 8),
        "snapshot_at": datetime(2026, 9, 8, tzinfo=timezone.utc),
        "baseline_decision": baseline_decision,
        "baseline_confidence": 0.7,
        "entry_range": (10.0, 11.0),
        "stop_loss": 9.0,
        "take_profit": (12.0, 13.0),
        "risk_reward": 2.0,
        "position_size_eur": 250.0,
        "time_stop_at": date(2026, 9, 18),
        "reasons": ["Momentum supports the baseline."],
        "warnings": [],
        "diagnosis": None,
        "analyst_views": [
            AnalystView(
                agent_name="technical_analyst",
                status="completed",
                overall_signal="positive",
                findings=[finding],
            )
        ],
        "known_metric_ids": ["rsi:2026-09-08"],
        "known_chunk_ids": [],
        "known_warning_codes": [],
        "allowed_decisions": allowed_decisions,
        "max_confidence": 0.7,
    }


def _response(request) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "run_id": request.run_id,
            "context_hash": request.context_hash,
            "proposed_decision": request.baseline_decision,
            "confidence": 0.7,
            "thesis": "The analyst view supports the deterministic baseline.",
            "key_reasons": [
                {
                    "claim": "RSI is 60 and momentum is stable.",
                    "metric_ids": ["rsi:2026-09-08"],
                    "chunk_ids": [],
                    "warning_codes": [],
                }
            ],
            "key_risks": [],
            "what_would_change": [],
        }
    )
