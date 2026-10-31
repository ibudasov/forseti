"""Backwards-compatible token pricing helpers backed by :mod:`app.services.cost_model`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.services.cost_model import price_catalog, price_usage
from app.settings import Settings


@dataclass(frozen=True)
class ModelTokenPricing:
    input_usd_per_million_tokens: float
    output_usd_per_million_tokens: float


def token_totals(token_usage: Mapping[str, int | float]) -> tuple[int, int, int]:
    prompt_tokens = int(token_usage.get("prompt_token_count", 0) or 0)
    candidate_tokens = int(token_usage.get("candidates_token_count", 0) or 0)
    total_tokens = int(token_usage.get("total_token_count", prompt_tokens + candidate_tokens) or 0)
    return prompt_tokens, candidate_tokens, total_tokens


def get_model_token_pricing(
    model_name: str,
    *,
    settings: Settings | None = None,
) -> ModelTokenPricing | None:
    entry = price_catalog(settings).entry_for(model_name)
    if entry is None or entry.unit != "per_million_tokens":
        return None
    return ModelTokenPricing(
        input_usd_per_million_tokens=entry.input,
        output_usd_per_million_tokens=entry.output,
    )


def estimate_usage_cost_usd(
    model_name: str,
    token_usage: Mapping[str, int | float],
    *,
    settings: Settings | None = None,
) -> float | None:
    return price_usage(model_name, token_usage, settings=settings).usd
