"""Grounded synthesis via Gemini: produces evidence-backed explanations."""
from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import DocumentChunk

logger = logging.getLogger(__name__)

# Hard rule: synthesis output must never contain trade parameters
_FORBIDDEN_FIELDS = frozenset(
    {"entry_low", "entry_high", "stop_loss", "take_profit", "position_size", "risk_reward"}
)


class EvidenceItem(BaseModel):
    """A single evidence-backed claim with citations."""

    claim: str
    chunk_ids: List[int] = Field(default_factory=list)


class SynthesisOutput(BaseModel):
    """Structured evidence output for a ticker analysis."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    bullish_drivers: List[EvidenceItem] = Field(default_factory=list)
    bearish_risks: List[EvidenceItem] = Field(default_factory=list)
    catalysts: List[EvidenceItem] = Field(default_factory=list)
    news_alignment: str = ""
    red_flags: List[EvidenceItem] = Field(default_factory=list)
    chunk_count: int = 0
    status: str = "ok"

    @model_validator(mode="after")
    def _guardrail_no_trade_params(self) -> "SynthesisOutput":
        """Verify no trade parameter field names leaked into synthesis output fields."""
        leaked = _FORBIDDEN_FIELDS & set(SynthesisOutput.model_fields)
        if leaked:
            raise ValueError(
                f"Guardrail: synthesis output must not contain trade parameters: {leaked}"
            )
        return self


def _build_prompt(ticker: str, chunks: List[DocumentChunk]) -> str:
    labeled_chunks = "\n\n".join(
        f"[CHUNK {chunk.id}] ({chunk.source_type}) {chunk.text[:600]}"
        for chunk in chunks
    )
    return f"""You are a financial research analyst. Your task is to analyse evidence about {ticker} and produce a structured JSON response.

HARD RULE: You must NEVER output, suggest, or calculate entry prices, stop-loss levels, take-profit levels, position sizes, or risk/reward ratios. Any such output is forbidden and will be rejected.

Evidence chunks:
{labeled_chunks}

Answer the following five questions using ONLY the evidence above. Cite the chunk IDs that support each claim.

1. What are the main BULLISH DRIVERS?
2. What are the main BEARISH RISKS?
3. What NEAR-TERM CATALYSTS matter?
4. Does RECENT NEWS support or weaken the setup?
5. Are there major RED FLAGS not visible in price action alone?

Respond with valid JSON matching this schema:
{{
  "bullish_drivers": [{{"claim": "...", "chunk_ids": [1, 2]}}],
  "bearish_risks": [{{"claim": "...", "chunk_ids": [3]}}],
  "catalysts": [{{"claim": "...", "chunk_ids": [4]}}],
  "news_alignment": "...",
  "red_flags": [{{"claim": "...", "chunk_ids": [5]}}]
}}"""


def _parse_llm_json(ticker: str, raw_json: str, chunks: List[DocumentChunk]) -> SynthesisOutput:
    import json

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("LLM returned invalid JSON for %s: %s", ticker, exc)
        return _insufficient_output(ticker, chunks)

    try:
        output = SynthesisOutput(ticker=ticker, chunk_count=len(chunks), **data)
    except Exception as exc:
        logger.warning("SynthesisOutput validation failed for %s: %s", ticker, exc)
        return _insufficient_output(ticker, chunks)

    return output


def _insufficient_output(ticker: str, chunks: List[DocumentChunk]) -> SynthesisOutput:
    return SynthesisOutput(
        ticker=ticker,
        chunk_count=len(chunks),
        status="insufficient_data",
    )


def synthesize(
    ticker: str,
    chunks: List[DocumentChunk],
    gemini_model: Optional[str] = None,
    vertex_project: Optional[str] = None,
    vertex_location: str = "us-central1",
) -> SynthesisOutput:
    """Call Gemini with the retrieved chunks and return a :class:`SynthesisOutput`.

    Falls back to ``status="insufficient_data"`` when chunks are empty,
    the LLM is unavailable, or the response fails validation.
    """
    if not chunks:
        return _insufficient_output(ticker, [])

    prompt = _build_prompt(ticker, chunks)

    if vertex_project is None:
        logger.info("VERTEX_AI_PROJECT not configured — skipping LLM synthesis for %s", ticker)
        return _insufficient_output(ticker, chunks)

    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel

        vertexai.init(project=vertex_project, location=vertex_location)
        model_name = gemini_model or "gemini-2.0-flash-001"
        model = GenerativeModel(model_name)

        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        raw_json = response.text
        logger.info(
            "Synthesis tokens — ticker=%s input=%s output=%s",
            ticker,
            getattr(response.usage_metadata, "prompt_token_count", "?"),
            getattr(response.usage_metadata, "candidates_token_count", "?"),
        )
        return _parse_llm_json(ticker, raw_json, chunks)
    except Exception as exc:
        logger.warning("Gemini synthesis failed for %s: %s", ticker, exc)
        return _insufficient_output(ticker, chunks)
