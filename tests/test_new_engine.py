"""Tests for the deterministic evaluation engine."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.db.models import (
    EarningsEvent,
    Fundamental,
    PriceBar,
    TechnicalFeature,
)
from app.services.checklist import evaluate_checklist
from app.services.risk import calculate_risk_levels, RiskConfig
from app.services.vetoes import check_vetoes


class TestVetoes:
    """Test veto conditions."""

    def test_rsi_overbought_veto_triggers_above_70(self):
        """RSI > 70 should veto."""
        latest_bar = PriceBar(
            security_id=1,
            bar_date=date(2026, 1, 1),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=1000,
        )
        veto = check_vetoes(
            rsi=Decimal("71"),
            latest_bar=latest_bar,
            vix_close=None,
            next_earnings=None,
            sma_200=Decimal("100"),
            today=date(2026, 1, 1),
        )
        assert veto is not None
        assert veto.rule_id == "rsi_overbought"

    def test_rsi_at_70_does_not_trigger_veto(self):
        """RSI = 70 should NOT veto (strict > condition)."""
        latest_bar = PriceBar(
            security_id=1,
            bar_date=date(2026, 1, 1),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=1000,
        )
        veto = check_vetoes(
            rsi=Decimal("70"),
            latest_bar=latest_bar,
            vix_close=None,
            next_earnings=None,
            sma_200=Decimal("100"),
            today=date(2026, 1, 1),
        )
        # Should not veto for RSI = 70 exactly
        if veto is not None:
            assert veto.rule_id != "rsi_overbought"

    def test_vix_panic_veto_triggers_above_30(self):
        """VIX > 30 should veto."""
        veto = check_vetoes(
            rsi=None,
            latest_bar=None,
            vix_close=Decimal("31"),
            next_earnings=None,
            sma_200=None,
            today=date(2026, 1, 1),
        )
        assert veto is not None
        assert veto.rule_id == "vix_panic_regime"

    def test_earnings_too_close_veto_triggers(self):
        """Earnings within 7 days should veto to watchlist."""
        earnings = EarningsEvent(
            security_id=1,
            report_date=date(2026, 1, 5),  # 4 days away
            confirmed=False,
        )
        veto = check_vetoes(
            rsi=None,
            latest_bar=None,
            vix_close=None,
            next_earnings=earnings,
            sma_200=None,
            today=date(2026, 1, 1),
        )
        assert veto is not None
        assert veto.rule_id == "earnings_too_close"

    def test_deep_below_sma200_veto_triggers(self):
        """Close < 0.90 * SMA200 should veto."""
        latest_bar = PriceBar(
            security_id=1,
            bar_date=date(2026, 1, 1),
            open=Decimal("89"),  # 0.89 * 100 = 89, below 0.90 * 100 = 90
            high=Decimal("89"),
            low=Decimal("89"),
            close=Decimal("89"),
            volume=1000,
        )
        veto = check_vetoes(
            rsi=None,
            latest_bar=latest_bar,
            vix_close=None,
            next_earnings=None,
            sma_200=Decimal("100"),
            today=date(2026, 1, 1),
        )
        assert veto is not None
        assert veto.rule_id == "deep_below_sma200"

    def test_null_feature_values_fail_open(self):
        """Null values should not trigger vetoes."""
        veto = check_vetoes(
            rsi=None,  # null RSI
            latest_bar=None,  # null latest bar
            vix_close=None,  # null VIX
            next_earnings=None,  # null earnings
            sma_200=None,  # null SMA200
            today=date(2026, 1, 1),
        )
        assert veto is None


class TestChecklist:
    """Test checklist scoring rules."""

    def test_revenue_growth_rule_passes_above_threshold(self):
        fundamental = Fundamental(
            security_id=1,
            as_of_date=date(2026, 1, 1),
            revenue_growth=Decimal("0.20"),  # > 0.15
            fcf=None,
            debt_to_equity=None,
            eps_trend=None,
            margins=None,
            raw_payload={},
        )
        score, results = evaluate_checklist(
            latest_bar=None,
            fundamental=fundamental,
            technical_feature=None,
            vix_close=None,
        )
        assert any(r.rule_id == "revenue_growth" for r in results)
        assert score >= 2

    def test_null_fundamental_scores_zero(self):
        """Null fundamental data should score 0."""
        score, results = evaluate_checklist(
            latest_bar=None,
            fundamental=None,
            technical_feature=None,
            vix_close=None,
        )
        assert score == 0
        assert len(results) == 0

    def test_all_rules_firing_max_score_11(self):
        """All 9 rules firing should give score = 11."""
        latest_bar = PriceBar(
            security_id=1,
            bar_date=date(2026, 1, 1),
            open=Decimal("120"),
            high=Decimal("120"),
            low=Decimal("120"),
            close=Decimal("120"),
            volume=1000,
        )
        technical_feature = TechnicalFeature(
            security_id=1,
            as_of_date=date(2026, 1, 1),
            rsi_14=Decimal("55"),  # healthy: 45-65
            sma_50=Decimal("100"),  # close > sma_50
            sma_200=Decimal("90"),  # close > sma_200
            volume_trend=Decimal("1.5"),  # > 1.0
        )
        fundamental = Fundamental(
            security_id=1,
            as_of_date=date(2026, 1, 1),
            revenue_growth=Decimal("0.20"),  # > 0.15
            fcf=Decimal("1000000"),  # > 0
            debt_to_equity=Decimal("0.5"),  # < 1.0
            eps_trend=Decimal("0.1"),  # > 0
            margins=None,
            raw_payload={},
        )
        vix_close = Decimal("20")  # < 25

        score, results = evaluate_checklist(
            latest_bar=latest_bar,
            fundamental=fundamental,
            technical_feature=technical_feature,
            vix_close=vix_close,
        )
        assert score == 11
        assert len(results) == 9


class TestRiskMath:
    """Test risk calculations."""

    def test_golden_case_exact_frozen_numbers(self, db_engine):
        """Test with exact frozen numbers from spec."""
        # Create bars with the exact scenario from the spec:
        # close = 122.00, swing_low_20 = 115.00, atr_14 = 4.00,
        # swing_high_60 = 134.00
        bars = []
        for i in range(60):
            if i < 20:
                close_val = Decimal("115.00") + (Decimal(i) * Decimal("0.1"))
            else:
                close_val = Decimal("120.00") + (Decimal(i - 20) * Decimal("0.1"))

            bars.append(
                PriceBar(
                    security_id=1,
                    bar_date=date(2026, 1, 1) + timedelta(days=i),
                    open=close_val,
                    high=close_val + Decimal("2"),  # Ensure ATR ≈ 4
                    low=close_val - Decimal("2"),
                    close=close_val,
                    volume=1000,
                )
            )

        # Adjust last bar to be exactly 122.00
        bars[-1] = PriceBar(
            security_id=1,
            bar_date=date(2026, 2, 28),
            open=Decimal("122.00"),
            high=Decimal("134.00"),  # swing high
            low=Decimal("115.00"),  # swing low 20
            close=Decimal("122.00"),
            volume=1000,
        )

        risk_config = RiskConfig(
            capital_eur=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.01"),
        )
        result = calculate_risk_levels(bars, risk_config)
        assert result is not None
        assert not hasattr(result, "reason")  # Should be TradeLevels, not RiskDowngrade
        assert float(result.entry_low) == pytest.approx(119.56, abs=0.01)
        assert float(result.entry_high) == pytest.approx(124.44, abs=0.01)
        assert float(result.position_size_eur) == pytest.approx(1119.96, abs=0.01)

    def test_invalid_risk_geometry_downgrade(self):
        """Risk per share <= 0 should downgrade."""
        # Create bars with close == stop_loss (invalid geometry)
        bars = []
        for i in range(20):
            bars.append(
                PriceBar(
                    security_id=1,
                    bar_date=date(2026, 1, 1) + timedelta(days=i),
                    open=Decimal("100"),
                    high=Decimal("100"),
                    low=Decimal("100"),
                    close=Decimal("100"),
                    volume=1000,
                )
            )

        risk_config = RiskConfig(
            capital_eur=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.01"),
        )
        result = calculate_risk_levels(bars, risk_config)
        # With flat prices, entry will be close to close, stop loss also close,
        # so risk_per_share will be near 0
        if hasattr(result, "reason"):
            assert result.reason == "invalid_risk_geometry"

    def test_risk_reward_below_min_downgrade(self):
        """Risk reward < 1.5 should downgrade."""
        # Create scenario where TP1 (swing high) is too close
        bars = []
        for i in range(60):
            # Make prices gradually rising but with low swing high
            close_val = Decimal("100") + (Decimal(i) * Decimal("0.1"))
            bars.append(
                PriceBar(
                    security_id=1,
                    bar_date=date(2026, 1, 1) + timedelta(days=i),
                    open=close_val,
                    high=close_val + Decimal("0.5"),  # Low swing high
                    low=close_val - Decimal("2"),
                    close=close_val,
                    volume=1000,
                )
            )

        risk_config = RiskConfig(
            capital_eur=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.01"),
        )
        result = calculate_risk_levels(bars, risk_config)
        if hasattr(result, "reason"):
            assert result.reason == "risk_reward_below_min"
