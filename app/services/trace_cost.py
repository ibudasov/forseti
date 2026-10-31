"""Prices one analysis run: every trace step with its own model plus embeddings."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.schemas.analyze import TraceStep
from app.services.cost_model import (
    CHARACTER_USAGE_FIELD,
    CostEstimate,
    UsageLine,
    price_lines,
)
from app.settings import Settings, get_settings

UNATTRIBUTED_STEP = "unattributed_model_calls"
EMBEDDING_STEP = "query_embeddings"


def model_for_agent(agent_name: str, settings: Settings) -> str:
    """Return the Gemini model an agent runs on (all agents share GEMINI_MODEL today)."""
    return settings.GEMINI_MODEL


def price_trace(
    steps: Sequence[TraceStep],
    total_token_usage: Mapping[str, int],
    *,
    embedding_model: str | None = None,
    embedding_characters: int = 0,
    settings: Settings | None = None,
) -> tuple[CostEstimate, dict[str, Any]]:
    """Return the run's cost estimate and the JSON-ready per-step breakdown."""
    configured_settings = settings or get_settings()
    lines = [
        UsageLine(
            f"{step.sequence}:{step.agent_name}",
            model_for_agent(step.agent_name, configured_settings),
            step.token_usage,
        )
        for step in steps
    ]
    unattributed = _unattributed_usage(steps, total_token_usage)
    if unattributed:
        lines.append(
            UsageLine(UNATTRIBUTED_STEP, configured_settings.GEMINI_MODEL, unattributed)
        )
    if embedding_model is not None and embedding_characters:
        lines.append(
            UsageLine(EMBEDDING_STEP, embedding_model, {CHARACTER_USAGE_FIELD: embedding_characters})
        )
    report = price_lines(lines, settings=configured_settings)
    breakdown = {
        "status": report.estimate.status,
        "unpriced_models": list(report.estimate.unpriced_models),
        "lines": list(report.breakdown),
    }
    return report.estimate, breakdown


def _unattributed_usage(
    steps: Sequence[TraceStep], total_token_usage: Mapping[str, int]
) -> dict[str, int]:
    """Usage in the run total that no trace step reports (typed services, revision loop)."""
    attributed: dict[str, int] = {}
    for step in steps:
        for field_name, value in step.token_usage.items():
            attributed[field_name] = attributed.get(field_name, 0) + value
    remainder = {
        field_name: total - attributed.get(field_name, 0)
        for field_name, total in total_token_usage.items()
    }
    return {field_name: value for field_name, value in remainder.items() if value > 0}
