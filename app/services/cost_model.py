"""Prices model usage per step with the versioned catalog in ``app/pricing``."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

from app.pricing.catalog import CatalogEntry, PriceCatalog, PriceRates, load_price_catalog
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

CostStatus = Literal["priced", "partially_priced", "unpriced"]
PRICED: CostStatus = "priced"
PARTIALLY_PRICED: CostStatus = "partially_priced"
UNPRICED: CostStatus = "unpriced"

TOKENS_PER_PRICE_UNIT = 1_000_000
CHARACTER_USAGE_FIELD = "characters"
UNPRICED_LABEL = "unpriced"

_warned_missing_models: set[str] = set()


@dataclass(frozen=True)
class CostEstimate:
    usd: float | None
    status: CostStatus
    unpriced_models: tuple[str, ...]
    pricing_version: str


@dataclass(frozen=True)
class UsageLine:
    """Usage of one billable call or step, priced with that call's own model."""

    step: str
    model: str
    usage: Mapping[str, int | float]


@dataclass(frozen=True)
class CostReport:
    estimate: CostEstimate
    breakdown: tuple[dict[str, Any], ...]


def price_catalog(settings: Settings | None = None) -> PriceCatalog:
    configured_settings = settings or get_settings()
    return load_price_catalog().with_overrides(configured_settings.MODEL_PRICING_USD_PER_MILLION)


def reset_missing_price_warnings() -> None:
    _warned_missing_models.clear()


def price_lines(lines: Iterable[UsageLine], *, settings: Settings | None = None) -> CostReport:
    catalog = price_catalog(settings)
    breakdown: list[dict[str, Any]] = []
    unpriced_models: list[str] = []
    priced_usd = 0.0
    priced_line_count = 0
    for line in lines:
        line_usd = _line_usd(catalog, line)
        breakdown.append(_breakdown_entry(line, line_usd))
        if line_usd is None:
            _note_missing_model(line.model, unpriced_models)
            continue
        priced_usd += line_usd
        priced_line_count += 1
    estimate = CostEstimate(
        usd=priced_usd if priced_line_count or not unpriced_models else None,
        status=_status(priced_line_count, unpriced_models),
        unpriced_models=tuple(unpriced_models),
        pricing_version=catalog.version,
    )
    return CostReport(estimate=estimate, breakdown=tuple(breakdown))


def price_usage(
    model: str,
    usage: Mapping[str, int | float],
    *,
    settings: Settings | None = None,
) -> CostEstimate:
    return price_lines([UsageLine(step="usage", model=model, usage=usage)], settings=settings).estimate


def format_cost_usd(estimate_usd: float | None) -> str:
    return UNPRICED_LABEL if estimate_usd is None else f"{estimate_usd:.6f}"


def check_pricing_coverage(settings: Settings | None = None) -> list[str]:
    """Log an ERROR for every configured model without a catalog entry; never raises."""
    configured_settings = settings or get_settings()
    catalog = price_catalog(configured_settings)
    configured_models = dict.fromkeys(
        (configured_settings.GEMINI_MODEL, configured_settings.EMBEDDING_MODEL)
    )
    missing = [model for model in configured_models if catalog.entry_for(model) is None]
    for model in missing:
        logger.error("pricing_coverage_missing:%s has no entry in the price catalog", model)
    return missing


def _status(priced_line_count: int, unpriced_models: list[str]) -> CostStatus:
    if not unpriced_models:
        return PRICED
    return PARTIALLY_PRICED if priced_line_count else UNPRICED


def _note_missing_model(model: str, unpriced_models: list[str]) -> None:
    if model not in unpriced_models:
        unpriced_models.append(model)
    if model in _warned_missing_models:
        return
    _warned_missing_models.add(model)
    logger.warning("pricing_missing:%s", model)


def _breakdown_entry(line: UsageLine, line_usd: float | None) -> dict[str, Any]:
    return {
        "step": line.step,
        "model": line.model,
        "token_usage": {key: int(value) for key, value in line.usage.items()},
        "usd": line_usd,
    }


def _line_usd(catalog: PriceCatalog, line: UsageLine) -> float | None:
    if not any(line.usage.values()):
        return 0.0
    entry = catalog.entry_for(line.model)
    if entry is None:
        return None
    if entry.unit == "per_million_characters":
        characters = int(line.usage.get(CHARACTER_USAGE_FIELD, 0) or 0)
        return characters * entry.input / TOKENS_PER_PRICE_UNIT
    return _token_usd(entry, line.usage)


def _token_usd(entry: CatalogEntry, usage: Mapping[str, int | float]) -> float:
    prompt_tokens = int(usage.get("prompt_token_count", 0) or 0)
    cached_tokens = min(int(usage.get("cached_content_token_count", 0) or 0), prompt_tokens)
    output_tokens = int(usage.get("candidates_token_count", 0) or 0) + int(
        usage.get("thoughts_token_count", 0) or 0
    )
    rates = _rates_for(entry, prompt_tokens)
    return (
        (prompt_tokens - cached_tokens) * rates.input
        + cached_tokens * rates.cached_input
        + output_tokens * rates.output
    ) / TOKENS_PER_PRICE_UNIT


def _rates_for(entry: CatalogEntry, prompt_tokens: int) -> PriceRates:
    long_context = entry.long_context
    if long_context is not None and prompt_tokens > long_context.threshold_tokens:
        return long_context
    return entry
