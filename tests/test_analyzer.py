from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.db.models import PriceBar
from app.services.analyzer import analyze_bars, validate_and_normalize_ticker


def _bar(bar_date: date, close: str) -> PriceBar:
    return PriceBar(
        security_id=1,
        bar_date=bar_date,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=1_000,
    )


def test_validate_and_normalize_ticker_uppercases_and_trims():
    assert validate_and_normalize_ticker(" nvda ") == "NVDA"


def test_validate_and_normalize_ticker_rejects_url_like_input():
    with pytest.raises(ValueError):
        validate_and_normalize_ticker("https://broker.example/NVDA")


def test_analyze_bars_without_data_returns_watchlist_warning():
    result = analyze_bars(
        ticker="NVDA",
        bars=[],
        account_size_eur=None,
        risk_pct=None,
        max_position_size_eur=None,
        as_of_date=None,
        notes=None,
        trace_id="trace-1",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert result.response.decision == "watchlist"
    assert result.response.confidence == 0.35
    assert "insufficient_price_data" in result.response.warnings


def test_analyze_bars_rising_price_returns_trade_with_coherent_risk_fields():
    result = analyze_bars(
        ticker="NVDA",
        bars=[_bar(date(2026, 1, 1), "100.0000"), _bar(date(2026, 1, 2), "102.5000")],
        account_size_eur=10_000,
        risk_pct=0.01,
        max_position_size_eur=400,
        as_of_date=None,
        notes=None,
        trace_id="trace-2",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    response = result.response
    assert response.decision == "trade"
    assert response.entry_range is not None
    assert response.stop_loss is not None
    assert response.take_profit is not None
    assert response.entry_range[0] < response.entry_range[1]
    assert response.stop_loss < response.entry_range[0]
    assert response.take_profit[0] < response.take_profit[1]
    assert response.risk_reward is not None
    assert response.risk_reward >= 1.5
    assert response.position_size_eur == 400.0


def test_analyze_bars_falling_price_returns_no_trade():
    result = analyze_bars(
        ticker="NVDA",
        bars=[_bar(date(2026, 1, 1), "100.0000"), _bar(date(2026, 1, 2), "99.0000")],
        account_size_eur=None,
        risk_pct=None,
        max_position_size_eur=None,
        as_of_date=None,
        notes=None,
        trace_id="trace-3",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert result.response.decision == "no_trade"
    assert "negative_price_momentum" in result.response.warnings


def test_analyze_bars_confidence_is_clamped_and_risk_reward_not_negative():
    result = analyze_bars(
        ticker="NVDA",
        bars=[_bar(date(2026, 1, 1), "100.0000"), _bar(date(2026, 1, 2), "101.5000")],
        account_size_eur=10_000,
        risk_pct=0.01,
        max_position_size_eur=None,
        as_of_date=None,
        notes=None,
        trace_id="trace-4",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert 0 <= result.response.confidence <= 1
    assert result.response.risk_reward is not None
    assert result.response.risk_reward >= 0
