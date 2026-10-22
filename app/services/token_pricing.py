from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.settings import Settings, get_settings


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
    configured_settings = settings or get_settings()
    price_mapping = configured_settings.MODEL_PRICING_USD_PER_MILLION.get(model_name)
    if price_mapping is None:
        return None
    return ModelTokenPricing(
        input_usd_per_million_tokens=float(price_mapping["input"]),
        output_usd_per_million_tokens=float(price_mapping["output"]),
    )


def estimate_usage_cost_usd(
    model_name: str,
    token_usage: Mapping[str, int | float],
    *,
    settings: Settings | None = None,
) -> float | None:
    pricing = get_model_token_pricing(model_name, settings=settings)
    if pricing is None:
        return None
    prompt_tokens, candidate_tokens, _ = token_totals(token_usage)
    return (
        prompt_tokens * pricing.input_usd_per_million_tokens
        + candidate_tokens * pricing.output_usd_per_million_tokens
    ) / 1_000_000
