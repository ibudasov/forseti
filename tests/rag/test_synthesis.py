"""Tests for synthesis module: prompt construction, schema validation, guardrails."""
from __future__ import annotations

import json
import sys
import types

import pytest

from app.db.models import DocumentChunk, SourceType
from app.rag.synthesis import (
    EvidenceItem,
    SynthesisOutput,
    _build_prompt,
    _insufficient_output,
    _parse_llm_json,
    synthesize,
)


def _make_chunk(chunk_id: int, text: str = "Some evidence text.") -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        ticker="NVDA",
        source_type=SourceType.filing_business,
        source_url="https://example.com",
        source_hash=f"hash{chunk_id}",
        chunk_index=0,
        text=text,
    )


class TestPromptConstruction:
    def test_prompt_contains_ticker(self):
        chunks = [_make_chunk(1)]
        prompt = _build_prompt("NVDA", chunks)
        assert "NVDA" in prompt

    def test_prompt_contains_chunk_text(self):
        chunks = [_make_chunk(1, text="Unique text about revenue growth.")]
        prompt = _build_prompt("NVDA", chunks)
        assert "Unique text about revenue growth" in prompt

    def test_prompt_contains_hard_rule(self):
        chunks = [_make_chunk(1)]
        prompt = _build_prompt("NVDA", chunks)
        # Guardrail must be present in the prompt
        assert "stop-loss" in prompt.lower() or "stop_loss" in prompt.lower() or "hard rule" in prompt.lower()


class TestSchemaValidation:
    def test_valid_synthesis_output_is_accepted(self):
        output = SynthesisOutput(
            ticker="NVDA",
            bullish_drivers=[EvidenceItem(claim="Strong AI revenue growth.", chunk_ids=[1])],
            bearish_risks=[EvidenceItem(claim="High competition.", chunk_ids=[2])],
            catalysts=[EvidenceItem(claim="Next earnings call.", chunk_ids=[3])],
            news_alignment="News is positive overall.",
            red_flags=[],
            chunk_count=3,
        )
        assert output.ticker == "NVDA"
        assert len(output.bullish_drivers) == 1

    def test_synthesis_output_defaults_to_ok_status(self):
        output = SynthesisOutput(ticker="AAPL", chunk_count=0)
        assert output.status == "ok"


class TestGuardrails:
    def test_synthesis_output_cannot_contain_stop_loss_field(self):
        """Guardrail: synthesis output must never carry trade parameter fields."""
        with pytest.raises(Exception):
            # Attempt to construct with a forbidden field name in the model
            SynthesisOutput.model_validate(
                {"ticker": "NVDA", "stop_loss": 100.0, "chunk_count": 0}
            )

    def test_forbidden_fields_constant_is_complete(self):
        from app.rag.synthesis import _FORBIDDEN_FIELDS

        expected = {"entry_low", "entry_high", "stop_loss", "take_profit", "position_size", "risk_reward"}
        assert expected == _FORBIDDEN_FIELDS


class TestParseLLMJson:
    def test_valid_json_produces_synthesis_output(self):
        chunks = [_make_chunk(1)]
        raw = json.dumps(
            {
                "bullish_drivers": [{"claim": "Strong data center demand.", "chunk_ids": [1]}],
                "bearish_risks": [{"claim": "Export restrictions.", "chunk_ids": [1]}],
                "catalysts": [],
                "news_alignment": "Mostly positive.",
                "red_flags": [],
            }
        )
        result = _parse_llm_json("NVDA", raw, chunks)
        assert result.status == "ok"
        assert len(result.bullish_drivers) == 1

    def test_invalid_json_raises(self):
        chunks = [_make_chunk(1)]
        with pytest.raises(ValueError, match="invalid JSON"):
            _parse_llm_json("NVDA", "not valid json", chunks)

    def test_empty_chunks_returns_insufficient(self):
        result = synthesize("NVDA", chunks=[], vertex_project=None)
        assert result.status == "insufficient_data"

    def test_no_vertex_project_returns_insufficient(self):
        chunks = [_make_chunk(1)]
        result = synthesize("NVDA", chunks=chunks, vertex_project=None)
        assert result.status == "insufficient_data"
        assert result.chunk_count == 1


class TestInsufficientOutput:
    def test_insufficient_output_sets_correct_status(self):
        chunks = [_make_chunk(1), _make_chunk(2)]
        output = _insufficient_output("NVDA", chunks)
        assert output.status == "insufficient_data"
        assert output.chunk_count == 2


class TestFailLoud:
    def _install_failing_gemini(self, monkeypatch):
        """Simulate a `vertexai` install whose Gemini call always raises."""
        fake_vertexai = types.ModuleType("vertexai")
        fake_vertexai.init = lambda **kwargs: None
        fake_generative_models = types.ModuleType("vertexai.generative_models")

        class _FailingModel:
            def __init__(self, model_name):
                pass

            def generate_content(self, prompt, generation_config=None):
                raise RuntimeError("gemini backend unavailable")

        fake_generative_models.GenerativeModel = _FailingModel
        fake_vertexai.generative_models = fake_generative_models
        monkeypatch.setitem(sys.modules, "vertexai", fake_vertexai)
        monkeypatch.setitem(sys.modules, "vertexai.generative_models", fake_generative_models)

    def test_fail_loud_propagates_gemini_failure(self, monkeypatch):
        self._install_failing_gemini(monkeypatch)
        chunks = [_make_chunk(1)]

        with pytest.raises(RuntimeError):
            synthesize("NVDA", chunks=chunks, vertex_project="proj", fail_loud=True)

    def test_gemini_failure_raises_even_without_legacy_flag(self, monkeypatch):
        self._install_failing_gemini(monkeypatch)
        chunks = [_make_chunk(1)]

        with pytest.raises(RuntimeError, match="gemini backend unavailable"):
            synthesize("NVDA", chunks=chunks, vertex_project="proj")
