"""Tests for ADK tool adapters: delegation, schema validation, error mapping."""
from __future__ import annotations

from datetime import date

from agents.tools.data_collector import build_structured_data_collector_tool
from agents.tools.risk_engine import build_risk_manager_tool
from agents.tools.rules_engine import build_rules_engine_tool
from agents.tools.ticker_resolver import resolve_ticker
from app.services.risk import RiskConfig


class TestResolveTicker:
    def test_valid_ticker_is_normalized(self):
        result = resolve_ticker(" nvda ")
        assert result.is_valid is True
        assert result.ticker == "NVDA"
        assert result.error is None

    def test_invalid_ticker_reports_error_without_raising(self):
        result = resolve_ticker("https://broker.example/NVDA")
        assert result.is_valid is False
        assert result.ticker == ""
        assert result.error is not None


class TestStructuredDataCollectorTool:
    def test_delegates_to_build_ticker_profile(self, monkeypatch):
        captured = {}

        def fake_build_ticker_profile(ticker, engine=None, today=None):
            captured["ticker"] = ticker
            captured["engine"] = engine
            captured["today"] = today
            return "profile-for-" + ticker

        monkeypatch.setattr(
            "agents.tools.data_collector.build_ticker_profile", fake_build_ticker_profile
        )
        sentinel_engine = object()
        collect = build_structured_data_collector_tool(engine=sentinel_engine, today=date(2024, 1, 1))

        result = collect("NVDA")

        assert result == "profile-for-NVDA"
        assert captured == {"ticker": "NVDA", "engine": sentinel_engine, "today": date(2024, 1, 1)}


class TestRulesEngineTool:
    def test_veto_short_circuits_checklist(self, monkeypatch):
        monkeypatch.setattr("agents.tools.rules_engine.get_latest_bars", lambda *a, **k: [])
        monkeypatch.setattr("agents.tools.rules_engine.get_latest_technical_feature", lambda *a, **k: None)
        monkeypatch.setattr("agents.tools.rules_engine.get_latest_fundamental", lambda *a, **k: None)
        monkeypatch.setattr("agents.tools.rules_engine.get_latest_macro_daily", lambda *a, **k: None)
        monkeypatch.setattr("agents.tools.rules_engine.get_next_earnings_event", lambda *a, **k: None)

        class _FakeVeto:
            rule_id = "rsi_overbought"
            detail = "rsi too high"

        monkeypatch.setattr("agents.tools.rules_engine.check_vetoes", lambda **k: _FakeVeto())

        evaluate = build_rules_engine_tool()
        result = evaluate("NVDA")

        assert result.veto_rule_id == "rsi_overbought"
        assert result.checklist_score == 0

    def test_no_veto_returns_checklist_score(self, monkeypatch):
        monkeypatch.setattr("agents.tools.rules_engine.get_latest_bars", lambda *a, **k: [])
        monkeypatch.setattr("agents.tools.rules_engine.get_latest_technical_feature", lambda *a, **k: None)
        monkeypatch.setattr("agents.tools.rules_engine.get_latest_fundamental", lambda *a, **k: None)
        monkeypatch.setattr("agents.tools.rules_engine.get_latest_macro_daily", lambda *a, **k: None)
        monkeypatch.setattr("agents.tools.rules_engine.get_next_earnings_event", lambda *a, **k: None)
        monkeypatch.setattr("agents.tools.rules_engine.check_vetoes", lambda **k: None)

        class _FakeResult:
            rule_id = "revenue_growth"
            detail = "0.20 > 0.15"

        monkeypatch.setattr(
            "agents.tools.rules_engine.evaluate_checklist", lambda **k: (2, [_FakeResult()])
        )

        evaluate = build_rules_engine_tool()
        result = evaluate("NVDA")

        assert result.veto_rule_id is None
        assert result.checklist_score == 2
        assert result.checklist_reasons == ["revenue_growth: 0.20 > 0.15"]


class TestRiskManagerTool:
    def test_returns_empty_output_when_geometry_unavailable(self, monkeypatch):
        monkeypatch.setattr("agents.tools.risk_engine.get_latest_bars", lambda *a, **k: [])
        monkeypatch.setattr("agents.tools.risk_engine.calculate_risk_levels", lambda *a, **k: None)

        calculate = build_risk_manager_tool(RiskConfig(capital_eur=10000, risk_per_trade_pct=0.01))
        result = calculate("NVDA")

        assert result.entry_low is None
        assert result.downgrade_reason is None

    def test_maps_downgrade_reason(self, monkeypatch):
        from app.services.risk import RiskDowngrade

        monkeypatch.setattr("agents.tools.risk_engine.get_latest_bars", lambda *a, **k: [])
        monkeypatch.setattr(
            "agents.tools.risk_engine.calculate_risk_levels",
            lambda *a, **k: RiskDowngrade(reason="invalid_risk_geometry", detail="bad geometry"),
        )

        calculate = build_risk_manager_tool(RiskConfig(capital_eur=10000, risk_per_trade_pct=0.01))
        result = calculate("NVDA")

        assert result.downgrade_reason == "invalid_risk_geometry"
        assert result.downgrade_detail == "bad geometry"
        assert result.entry_low is None
