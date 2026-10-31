"""Versioned model price catalog loaded from ``catalog.json``."""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

CATALOG_PATH = Path(__file__).with_name("catalog.json")
ENV_OVERRIDE_SOURCE = "env:MODEL_PRICING_USD_PER_MILLION"
ENV_OVERRIDE_SUFFIX = "+env_override"

PriceUnit = Literal["per_million_tokens", "per_million_characters"]


class PriceRates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input: float = Field(ge=0)


class LongContextRates(PriceRates):
    threshold_tokens: int = Field(gt=0)


class CatalogEntry(PriceRates):
    model: str = Field(min_length=1)
    unit: PriceUnit
    long_context: LongContextRates | None = None
    effective_from: date
    source_url: str = Field(min_length=1)


class PriceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    entries: tuple[CatalogEntry, ...]

    @model_validator(mode="after")
    def _models_are_unique(self) -> "PriceCatalog":
        names = [entry.model for entry in self.entries]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise ValueError(f"duplicate catalog models: {duplicated}")
        return self

    def entry_for(self, model: str) -> CatalogEntry | None:
        return next((entry for entry in self.entries if entry.model == model), None)

    def with_overrides(self, overrides: Mapping[str, Mapping[str, float]]) -> "PriceCatalog":
        if not overrides:
            return self
        replaced = {entry.model: entry for entry in self.entries}
        for model, rates in overrides.items():
            replaced[model] = _override_entry(model, rates)
        return PriceCatalog(
            version=self.version + ENV_OVERRIDE_SUFFIX,
            entries=tuple(replaced.values()),
        )


def _override_entry(model: str, rates: Mapping[str, float]) -> CatalogEntry:
    input_rate = float(rates["input"])
    return CatalogEntry(
        model=model,
        unit="per_million_tokens",
        input=input_rate,
        output=float(rates["output"]),
        cached_input=float(rates.get("cached_input", input_rate)),
        effective_from=date.today(),
        source_url=ENV_OVERRIDE_SOURCE,
    )


@lru_cache(maxsize=1)
def load_price_catalog(path: Path = CATALOG_PATH) -> PriceCatalog:
    return PriceCatalog.model_validate(json.loads(path.read_text(encoding="utf-8")))
