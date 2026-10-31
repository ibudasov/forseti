from __future__ import annotations

import json
import logging

import pytest
from pydantic import ValidationError

from app.pricing.catalog import CATALOG_PATH, PriceCatalog, load_price_catalog
from app.services import cost_model
from app.services.cost_model import (
    UsageLine,
    check_pricing_coverage,
    format_cost_usd,
    price_lines,
    price_usage,
)
from app.services.token_pricing import estimate_usage_cost_usd
from app.settings import Settings


@pytest.fixture(autouse=True)
def reset_warnings():
    cost_model.reset_missing_price_warnings()


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_mixed_models_are_priced_per_step():
    report = price_lines(
        [
            UsageLine("fundamental", "gemini-2.0-flash-001", {"prompt_token_count": 1_000_000}),
            UsageLine("critic", "gemini-2.5-flash", {"candidates_token_count": 1_000_000}),
        ],
        settings=settings(),
    )

    assert report.estimate.status == "priced"
    assert report.estimate.usd == pytest.approx(0.10 + 2.50)
    assert [entry["usd"] for entry in report.breakdown] == [pytest.approx(0.10), pytest.approx(2.50)]


def test_thoughts_are_billed_as_output_and_cached_at_cached_rate():
    usage = {
        "prompt_token_count": 1_000_000,
        "cached_content_token_count": 400_000,
        "candidates_token_count": 100_000,
        "thoughts_token_count": 200_000,
    }

    estimate = price_usage("gemini-2.5-flash-lite", usage, settings=settings())

    expected = 0.6 * 0.10 + 0.4 * 0.01 + 0.3 * 0.40
    assert estimate.usd == pytest.approx(expected)


def test_long_context_rates_apply_above_threshold():
    below = price_usage("gemini-2.5-pro", {"prompt_token_count": 200_000}, settings=settings())
    above = price_usage("gemini-2.5-pro", {"prompt_token_count": 200_001}, settings=settings())

    assert below.usd == pytest.approx(0.2 * 1.25)
    assert above.usd == pytest.approx(0.200001 * 2.50)


def test_embeddings_are_priced_in_characters():
    estimate = price_usage("text-embedding-004", {"characters": 2_000_000}, settings=settings())

    assert estimate.usd == pytest.approx(0.05)


def test_unknown_model_is_unpriced_and_warns_once(caplog):
    usage = {"prompt_token_count": 10}
    with caplog.at_level(logging.WARNING, logger=cost_model.logger.name):
        first = price_usage("mystery-model", usage, settings=settings())
        second = price_usage("mystery-model", usage, settings=settings())

    assert first.usd is None
    assert first.status == "unpriced"
    assert first.unpriced_models == ("mystery-model",)
    assert second.status == "unpriced"
    assert [r.getMessage() for r in caplog.records] == ["pricing_missing:mystery-model"]


def test_partially_priced_when_one_model_is_unknown():
    report = price_lines(
        [
            UsageLine("a", "gemini-2.5-flash-lite", {"prompt_token_count": 1_000_000}),
            UsageLine("b", "mystery-model", {"prompt_token_count": 5}),
        ],
        settings=settings(),
    )

    assert report.estimate.status == "partially_priced"
    assert report.estimate.usd == pytest.approx(0.10)
    assert report.estimate.unpriced_models == ("mystery-model",)


def test_zero_token_steps_cost_zero_even_for_unknown_models():
    report = price_lines([UsageLine("typed_view", "mystery-model", {})], settings=settings())

    assert report.estimate.usd == 0.0
    assert report.estimate.status == "priced"


def test_estimate_wrapper_matches_previous_values_for_gemini_2_0_flash():
    usage = {"prompt_token_count": 1000, "candidates_token_count": 500}

    assert estimate_usage_cost_usd("gemini-2.0-flash-001", usage, settings=settings()) == pytest.approx(
        (1000 * 0.10 + 500 * 0.40) / 1_000_000
    )
    assert estimate_usage_cost_usd("mystery-model", usage, settings=settings()) is None


def test_env_override_replaces_catalog_rates_and_marks_version():
    overridden = settings(MODEL_PRICING_USD_PER_MILLION={"mystery-model": {"input": 1.0, "output": 2.0}})

    estimate = price_usage("mystery-model", {"prompt_token_count": 1_000_000}, settings=overridden)

    assert estimate.usd == pytest.approx(1.0)
    assert estimate.pricing_version.endswith("+env_override")


def test_shipped_catalog_covers_default_models_and_validates():
    catalog = load_price_catalog()

    assert catalog.entry_for("gemini-2.5-flash-lite") is not None
    assert check_pricing_coverage(settings()) == []


def test_catalog_rejects_duplicates_and_missing_fields():
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    duplicated = {**payload, "entries": payload["entries"] + payload["entries"][:1]}
    missing_source = {**payload, "entries": [{k: v for k, v in payload["entries"][0].items() if k != "source_url"}]}

    with pytest.raises(ValidationError):
        PriceCatalog.model_validate(duplicated)
    with pytest.raises(ValidationError):
        PriceCatalog.model_validate(missing_source)


def test_coverage_check_logs_error_for_unconfigured_model(caplog):
    with caplog.at_level(logging.ERROR, logger=cost_model.logger.name):
        missing = check_pricing_coverage(settings(GEMINI_MODEL="gemini-9-ultra"))

    assert missing == ["gemini-9-ultra"]
    assert "gemini-9-ultra" in caplog.text


def test_format_cost_usd_shows_unpriced_explicitly():
    assert format_cost_usd(None) == "unpriced"
    assert format_cost_usd(0.0) == "0.000000"


def test_trace_cost_prices_unattributed_total_usage_and_embeddings():
    from app.schemas.analyze import TraceStep
    from app.services.trace_cost import price_trace

    steps = [TraceStep(sequence=1, agent_name="a", status="completed", token_usage={"prompt_token_count": 100})]

    estimate, breakdown = price_trace(
        steps,
        {"prompt_token_count": 300},
        embedding_model="text-embedding-004",
        embedding_characters=1_000_000,
        settings=settings(),
    )

    steps_priced = {line["step"]: line["token_usage"] for line in breakdown["lines"]}
    assert steps_priced["unattributed_model_calls"] == {"prompt_token_count": 200}
    assert steps_priced["query_embeddings"] == {"characters": 1_000_000}
    assert estimate.usd == pytest.approx(300 * 0.10 / 1_000_000 + 0.025)


def test_model_token_pricing_wrapper_reads_catalog_rates():
    from app.services.token_pricing import get_model_token_pricing

    pricing = get_model_token_pricing("gemini-2.0-flash-001", settings=settings())

    assert (pricing.input_usd_per_million_tokens, pricing.output_usd_per_million_tokens) == (0.10, 0.40)
    assert get_model_token_pricing("text-embedding-004", settings=settings()) is None
    assert get_model_token_pricing("mystery-model", settings=settings()) is None
