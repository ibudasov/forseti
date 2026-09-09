"""Tests for analyzer module."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.db.models import EarningsEvent, Fundamental, PriceBar, TechnicalFeature
from app.services.analyzer import analyze, validate_and_normalize_ticker


def test_validate_and_normalize_ticker_uppercases_and_trims():
    assert validate_and_normalize_ticker(" nvda ") == "NVDA"


def test_validate_and_normalize_ticker_rejects_url_like_input():
    with pytest.raises(ValueError):
        validate_and_normalize_ticker("https://broker.example/NVDA")


def test_validate_and_normalize_ticker_rejects_empty():
    with pytest.raises(ValueError):
        validate_and_normalize_ticker("   ")


def test_validate_and_normalize_ticker_rejects_too_long():
    with pytest.raises(ValueError):
        validate_and_normalize_ticker("TOOLONGTICKER")


def test_validate_and_normalize_ticker_rejects_invalid_chars():
    with pytest.raises(ValueError):
        validate_and_normalize_ticker("NV$DA")


def _active_security(is_active: bool = True):
    return SimpleNamespace(id=1, is_active=is_active)


def _build_bars(
    count: int,
    latest_close: Decimal = Decimal("100"),
    latest_bar_date: date = date(2026, 1, 1),
):
    bars = []
    start_date = latest_bar_date - timedelta(days=count - 1)
    for index in range(count):
        close = latest_close if index == count - 1 else Decimal("100")
        bars.append(
            PriceBar(
                security_id=1,
                bar_date=start_date + timedelta(days=index),
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=1_000_000,
            )
        )
    return bars


def _patch_analyzer_dependencies(
    monkeypatch,
    *,
    security,
    bars,
    technical_feature=None,
    fundamental=None,
    vix=None,
    earnings_event=None,
):
    monkeypatch.setattr("app.services.analyzer.get_security", lambda symbol, engine=None: security)
    monkeypatch.setattr(
        "app.services.analyzer.get_latest_bars",
        lambda symbol, limit, engine=None: list(reversed(bars)),
    )
    monkeypatch.setattr(
        "app.services.analyzer.get_latest_technical_feature",
        lambda symbol, engine=None: technical_feature,
    )
    monkeypatch.setattr(
        "app.services.analyzer.get_latest_fundamental",
        lambda symbol, engine=None: fundamental,
    )
    monkeypatch.setattr(
        "app.services.analyzer.get_latest_macro_daily",
        lambda engine=None: SimpleNamespace(vix=vix) if vix is not None else None,
    )
    monkeypatch.setattr(
        "app.services.analyzer.get_next_earnings_event",
        lambda symbol, on_or_after, engine=None: (
            earnings_event
            if earnings_event is not None and earnings_event.report_date >= on_or_after
            else None
        ),
    )


def test_analyze_unknown_security_returns_unknown_security_diagnosis(monkeypatch):
    monkeypatch.setattr("app.services.analyzer.get_security", lambda symbol, engine=None: None)

    response = analyze("NVDA", today=date(2026, 1, 1))

    assert response.decision == "no_trade"
    assert response.diagnosis is not None
    assert response.diagnosis.stage == "unknown_security"
    assert response.diagnosis.debug_reason == "unknown_security/ticker_not_found: security not found: NVDA"


def test_analyze_without_price_bars_returns_data_gate_no_price_data(monkeypatch):
    _patch_analyzer_dependencies(
        monkeypatch,
        security=_active_security(),
        bars=[],
    )

    response = analyze("NVDA", today=date(2026, 1, 1))

    assert response.decision == "no_trade"
    assert response.warnings == ["no_price_data"]
    assert response.diagnosis is not None
    assert response.diagnosis.stage == "data_gate"
    assert response.diagnosis.rule_id == "no_price_data"
    assert response.diagnosis.debug_reason == "data_gate/no_price_data: no price data available"


def test_analyze_with_one_bar_keeps_watchlist_behavior_and_reason(monkeypatch):
    _patch_analyzer_dependencies(
        monkeypatch,
        security=_active_security(),
        bars=_build_bars(1),
    )

    response = analyze("NVDA", today=date(2026, 1, 1))

    assert response.decision == "watchlist"
    assert response.warnings == ["insufficient_price_data"]
    assert response.diagnosis is not None
    assert response.diagnosis.stage == "data_gate"
    assert response.diagnosis.rule_id == "insufficient_price_data"
    assert response.diagnosis.debug_reason == (
        "data_gate/insufficient_price_data: fewer than 200 price bars available"
    )


def test_analyze_veto_case_reports_hard_veto_diagnosis(monkeypatch):
    technical_feature = TechnicalFeature(
        security_id=1,
        as_of_date=date(2026, 1, 1),
        rsi_14=Decimal("71"),
        sma_50=Decimal("95"),
        sma_200=Decimal("90"),
        volume_trend=Decimal("1.2"),
    )
    fundamental = Fundamental(
        security_id=1,
        as_of_date=date(2025, 12, 31),
        revenue_growth=Decimal("0.2"),
        fcf=Decimal("1000000"),
        debt_to_equity=Decimal("0.5"),
        eps_trend=Decimal("0.1"),
        margins=Decimal("0.3"),
        raw_payload={},
    )
    earnings_event = EarningsEvent(
        security_id=1,
        report_date=date(2026, 2, 1),
        confirmed=False,
    )
    _patch_analyzer_dependencies(
        monkeypatch,
        security=_active_security(),
        bars=_build_bars(250),
        technical_feature=technical_feature,
        fundamental=fundamental,
        vix=Decimal("20"),
        earnings_event=earnings_event,
    )

    response = analyze("NVDA", today=date(2026, 1, 1))

    assert response.decision == "no_trade"
    assert response.diagnosis is not None
    assert response.diagnosis.stage == "hard_veto"
    assert response.diagnosis.rule_id == "rsi_overbought"
    assert response.diagnosis.debug_reason == "hard_veto/rsi_overbought: rsi_14: 71.00 > 70 max"


def test_analyze_full_data_zero_score_explains_silent_no_trade(monkeypatch):
    technical_feature = TechnicalFeature(
        security_id=1,
        as_of_date=date(2026, 1, 1),
        rsi_14=Decimal("70"),
        sma_50=Decimal("105"),
        sma_200=Decimal("110"),
        volume_trend=Decimal("1.0"),
    )
    fundamental = Fundamental(
        security_id=1,
        as_of_date=date(2025, 12, 31),
        revenue_growth=Decimal("0.0"),
        fcf=Decimal("0"),
        debt_to_equity=Decimal("2.0"),
        eps_trend=Decimal("0.0"),
        margins=Decimal("0.1"),
        raw_payload={},
    )
    earnings_event = EarningsEvent(
        security_id=1,
        report_date=date(2026, 2, 1),
        confirmed=False,
    )
    _patch_analyzer_dependencies(
        monkeypatch,
        security=_active_security(),
        bars=_build_bars(250),
        technical_feature=technical_feature,
        fundamental=fundamental,
        vix=Decimal("30"),
        earnings_event=earnings_event,
    )

    response = analyze("NVDA", today=date(2026, 1, 1))

    assert response.decision == "no_trade"
    assert response.warnings == []
    assert response.confidence == 0.0
    assert response.diagnosis is not None
    assert response.diagnosis.stage == "checklist"
    assert response.diagnosis.checklist_score == 0
    assert response.diagnosis.debug_reason == (
        "checklist/score_below_watchlist: score 0/11, missing: none"
    )
