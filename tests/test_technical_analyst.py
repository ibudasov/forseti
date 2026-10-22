from __future__ import annotations

import json
import sys
import types
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.technicals import DeterministicTechnicalAnalysis, TechnicalMetricRef, TechnicalRuleResult
from app.schemas.technicals import (
    TechnicalAnalysisRequest,
    TechnicalAssessmentResponse,
    TechnicalContextCoverage,
    TechnicalFact,
)
from app.services.technical_analyst import (
    GeminiTechnicalModel,
    ModelOutput,
    RetryableModelError,
    TechnicalAnalyst,
    _technical_response_schema,
    build_technical_analyst_prompt,
)


class _FakeModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.model_name = "fake-gemini"

    def generate(self, prompt: str) -> ModelOutput:
        del prompt
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return ModelOutput(text=output, token_usage={"total_token_count": 9})


class _AssertNeverCalledModel:
    model_name = "never-called"

    def generate(self, prompt: str) -> ModelOutput:
        del prompt
        raise AssertionError("model must not be called when coverage is insufficient")


def test_gemini_model_enforces_technical_response_schema(monkeypatch):
    request = _request()
    captured_config = {}
    fake_vertexai = types.ModuleType("vertexai")
    fake_vertexai.init = lambda **kwargs: None
    fake_generative_models = types.ModuleType("vertexai.generative_models")

    class _FakeGemini:
        def __init__(self, model_name):
            del model_name

        def generate_content(self, prompt, generation_config):
            del prompt
            captured_config.update(generation_config)
            return types.SimpleNamespace(text=_response_json(request), usage_metadata=None)

    class _FakeGenerationConfig:
        @classmethod
        def from_dict(cls, config):
            return config

    fake_generative_models.GenerativeModel = _FakeGemini
    fake_generative_models.GenerationConfig = _FakeGenerationConfig
    monkeypatch.setitem(sys.modules, "vertexai", fake_vertexai)
    monkeypatch.setitem(sys.modules, "vertexai.generative_models", fake_generative_models)
    model = GeminiTechnicalModel(
        model_name="fake-gemini",
        vertex_project="project",
        vertex_location="region",
        temperature=0.2,
    )

    model.generate("prompt")

    assert captured_config["response_mime_type"] == "application/json"
    assert captured_config["response_schema"] == _technical_response_schema()
    assert "$defs" not in captured_config["response_schema"]
    assert "$ref" not in json.dumps(captured_config["response_schema"])
    assert "additionalProperties" not in json.dumps(captured_config["response_schema"])
    assert '"const"' not in json.dumps(captured_config["response_schema"])


def test_valid_assessment_is_accepted():
    request = _request()
    analyst = TechnicalAnalyst(model_port=_FakeModel([_response_json(request)]), max_retries=0)

    result = analyst.assess(request)

    assert isinstance(result.response, TechnicalAssessmentResponse)
    assert result.validation.accepted is True
    assert result.response.status == "completed"
    assert result.response.proposed_score_adjustment == -1
    assert result.token_usage == {"total_token_count": 9}
    assert result.latency_ms >= 0


def test_unknown_metric_citation_is_rejected():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["risk_flags"][0]["metric_ids"] = ["unknown_metric:2026-09-08"]
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert "unknown_metric_citation" in result.validation.reason_codes
    assert result.response.status == "failed"
    assert result.response.proposed_score_adjustment == 0


def test_material_uncited_finding_fails_schema_validation():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["risk_flags"][0]["metric_ids"] = []
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["schema_validation_failed"]


def test_uncited_numeric_claim_is_rejected():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["risk_flags"][0]["claim"] = "RSI has dropped sharply to 72.3, a fabricated reading."
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["uncited_numeric_claim"]


def test_claim_numbers_accept_fraction_or_percent_form_of_pct_facts():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["risk_flags"][0]["claim"] = "Price is 0.0523 below the 50-day SMA, near swing_low_20 at 100.50."
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is True


def test_negative_adjustment_without_support_is_rejected():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["risk_flags"] = []
    payload["overall_signal"] = "neutral"
    payload["summary"] = "No supporting evidence despite a negative adjustment."
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["negative_adjustment_without_support"]


def test_out_of_range_adjustment_is_rejected():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["proposed_score_adjustment"] = -3
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["adjustment_out_of_bounds"]


def test_duplicate_finding_ids_are_rejected():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["contradictions"] = [dict(payload["risk_flags"][0])]
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["schema_validation_failed"]


def test_forbidden_risk_field_is_rejected():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["stop_loss"] = 1.0
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["forbidden_response_field:stop_loss"]


def test_run_id_and_context_hash_mismatches_are_rejected():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["run_id"] = "other-run"
    payload["context_hash"] = "other-hash"
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["run_id_mismatch", "context_hash_mismatch"]


def test_malformed_json_retries_once_then_succeeds():
    request = _request()
    analyst = TechnicalAnalyst(
        model_port=_FakeModel(["not json", _response_json(request)]),
        max_retries=1,
    )

    result = analyst.assess(request)

    assert result.validation.accepted is True
    assert result.response.proposed_score_adjustment == -1


def test_malformed_json_without_retry_falls_back_to_neutral():
    request = _request()
    analyst = TechnicalAnalyst(model_port=_FakeModel(["not json"]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["malformed_json"]
    assert result.response.status == "failed"


def test_schema_violation_is_rejected():
    request = _request()
    payload = json.loads(_response_json(request))
    del payload["summary"]
    analyst = TechnicalAnalyst(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["schema_validation_failed"]


def test_retryable_model_failure_falls_back_to_neutral():
    request = _request()
    analyst = TechnicalAnalyst(
        model_port=_FakeModel([RetryableModelError("timeout"), RetryableModelError("timeout")]),
        max_retries=1,
    )

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["provider_error:timeout"]
    assert result.response.status == "failed"


def test_prompt_includes_guardrails_and_request_payload():
    request = _request()
    prompt = build_technical_analyst_prompt(request)

    assert "Never compute new indicator values" in prompt
    assert "Never output markdown" in prompt
    assert '"agent_name": "technical_analyst"' in prompt
    assert request.context_hash in prompt
    assert "downgrade-only" in prompt


@pytest.mark.parametrize(
    "coverage_kwargs",
    [
        {"required_facts_missing": ["dist_sma50_pct"]},
        {"bars_stale": True},
    ],
)
def test_insufficient_coverage_short_circuits_without_calling_model(coverage_kwargs):
    request = _request(coverage_kwargs=coverage_kwargs)
    analyst = TechnicalAnalyst(model_port=_AssertNeverCalledModel(), max_retries=0)

    result = analyst.assess(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["insufficient_coverage"]
    assert result.response.status == "insufficient_data"
    assert result.response.proposed_score_adjustment == 0
    assert result.response.trend_state == "unknown"
    assert result.response.momentum_state == "unknown"


def test_raw_output_is_truncated_to_four_thousand_characters():
    request = _request()
    payload = json.loads(_response_json(request))
    payload["summary"] = "x" * 5000
    raw_output = json.dumps(payload)
    analyst = TechnicalAnalyst(model_port=_FakeModel([raw_output]), max_retries=0)

    result = analyst.assess(request)

    assert len(result.raw_output) == 4001
    assert result.raw_output.endswith("…")
    assert len(raw_output) > 4000


def _request(
    *,
    coverage_kwargs: dict | None = None,
) -> TechnicalAnalysisRequest:
    facts = [
        TechnicalFact(
            metric_id="dist_sma50_pct:2026-09-08",
            name="dist_sma50_pct",
            value=Decimal("0.0523"),
            unit="pct",
            derivation="(close - sma_50) / sma_50",
        ),
        TechnicalFact(
            metric_id="swing_low_20:2026-09-08",
            name="swing_low_20",
            value=Decimal("100.50"),
            unit="price",
            derivation="min(low) over the last 20 bars",
        ),
    ]
    coverage_fields = {
        "bar_count": 200,
        "required_facts_missing": [],
        "latest_bar_date": date(2026, 9, 8),
        "bars_stale": False,
        "series_truncated": False,
    }
    coverage_fields.update(coverage_kwargs or {})
    return TechnicalAnalysisRequest(
        run_id="run-1",
        context_hash="ctx-hash",
        ticker="NVDA",
        sector="ai",
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
        as_of_date=date(2026, 9, 8),
        deterministic_result=DeterministicTechnicalAnalysis(
            ticker="NVDA",
            as_of_date=date(2026, 9, 8),
            score=1,
            maximum_score=5,
            rule_results=[
                TechnicalRuleResult(
                    rule_id="rsi_healthy",
                    status="passed",
                    points_awarded=1,
                    points_available=1,
                    metric_ids=["rsi_14:2026-09-08"],
                    explanation="rsi_14 55.00 within healthy range",
                )
            ],
            warnings=[],
            metrics=[
                TechnicalMetricRef(
                    metric_id="rsi_14:2026-09-08",
                    metric_name="rsi_14",
                    value=Decimal("55.00"),
                    unit="rsi_points",
                    as_of_date=date(2026, 9, 8),
                )
            ],
        ),
        facts=facts,
        series=[],
        coverage=TechnicalContextCoverage(**coverage_fields),
    )


def _response_json(request: TechnicalAnalysisRequest) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "run_id": request.run_id,
            "context_hash": request.context_hash,
            "agent_name": "technical_analyst",
            "status": "completed",
            "overall_signal": "negative",
            "trend_state": "range",
            "momentum_state": "weakening",
            "proposed_score_adjustment": -1,
            "findings": [],
            "contradictions": [],
            "risk_flags": [
                {
                    "finding_id": "risk-1",
                    "category": "support_resistance",
                    "direction": "negative",
                    "materiality": "high",
                    "claim": "Price is 5.23% below the 50-day SMA, near swing_low_20 at 100.50.",
                    "metric_ids": ["dist_sma50_pct:2026-09-08", "swing_low_20:2026-09-08"],
                }
            ],
            "missing_information": [],
            "summary": "Momentum is weakening near a key support level.",
        }
    )
