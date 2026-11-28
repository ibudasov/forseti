"""Unit tests for the live / ops product scorecard (`app/services/live_scorecard.py`)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from app.schemas.diagnostics import UniverseCoverage, UniverseDiagnosticsResponse
from app.services.live_scorecard import SHADOW_PROMOTION_TARGET_RUNS, build_live_scorecard


def _diagnostics(
    *,
    universe_size: int = 10,
    bars_200: int = 8,
    fundamentals: int = 7,
    earnings: int = 6,
    chunks: int = 5,
    macro_date: date | None = date(2026, 11, 20),
    blocked_by: dict | None = None,
) -> UniverseDiagnosticsResponse:
    return UniverseDiagnosticsResponse(
        generated_at=datetime(2026, 11, 24, tzinfo=timezone.utc),
        engine_version="v1.rules.0",
        universe_size=universe_size,
        coverage=UniverseCoverage(
            tickers_with_price_bars=universe_size,
            tickers_with_200_bars=bars_200,
            tickers_with_technical_features=bars_200,
            tickers_with_fundamentals=fundamentals,
            tickers_with_earnings_events=earnings,
            tickers_with_document_chunks=chunks,
            latest_macro_daily_date=macro_date,
        ),
        blocked_by=blocked_by
        or {
            "data_gate": {"insufficient_price_data": 2},
            "hard_veto": {"rsi_overbought": 1},
            "checklist": {"score_below_watchlist": 5},
            "risk_math": {},
            "passed": 2,
        },
        items=[],
    )


def test_build_live_scorecard_aggregates_coverage_shadow_and_backtest():
    scorecard = build_live_scorecard(
        _diagnostics(),
        fundamental_shadow={
            "run_count": 25,
            "outcome_counts": {"downgrade": 5, "neutral": 20},
            "latency_ms": {"p50": 420.5, "p90": 800.0},
            "token_usage": {"estimated_cost_usd": 0.0125},
        },
        technical_shadow={
            "run_count": 40,
            "decision_change_rate": 0.25,
            "latency_ms": {"p50": 310.0, "p90": 600.0},
            "token_usage": {"estimated_cost_usd": 0.0080},
        },
        critic_shadow={
            "run_count": 15,
            "decision_change_rate": 0.2,
        },
        technical_backtest={
            "horizon_trading_days": 20,
            "downgrade": {"sample_size": 10, "mean_forward_return": -0.025},
            "neutral": {"sample_size": 30, "mean_forward_return": 0.041},
            "excluded_insufficient_bars_count": 3,
        },
    )

    payload = json.loads(scorecard.as_json())
    assert payload["universe"]["universe_size"] == 10
    assert payload["universe"]["tickers_with_200_bars"] == 8
    assert payload["universe"]["blocked_by"] == {
        "checklist/score_below_watchlist": 5,
        "data_gate/insufficient_price_data": 2,
        "hard_veto/rsi_overbought": 1,
        "passed": 2,
    }
    assert len(payload["shadow_agents"]) == 3
    assert payload["shadow_agents"][0]["agent"] == "fundamental"
    assert payload["shadow_agents"][0]["run_count"] == 25
    assert payload["shadow_agents"][0]["target_runs"] == SHADOW_PROMOTION_TARGET_RUNS
    assert payload["shadow_agents"][0]["decision_change_rate"] == 0.2
    assert payload["backtest"]["downgrade_mean_return"] == -0.025
    assert payload["backtest"]["neutral_mean_return"] == 0.041

    markdown = scorecard.as_markdown()
    assert "## Live Product Scorecard" in markdown
    assert "### Universe Data Coverage" in markdown
    assert "8/10 | 80.0%" in markdown
    assert "### Screening Outcomes (`blocked_by`)" in markdown
    assert "`data_gate/insufficient_price_data` | 2" in markdown
    assert "### Shadow-Mode Agent Progress" in markdown
    assert f"`fundamental` | 25/{SHADOW_PROMOTION_TARGET_RUNS} | 20.0% | 420.5 ms | 0.0125" in markdown
    assert "### Technical Shadow Backtest (20-day forward return)" in markdown
    assert "-2.50%" in markdown
    assert "+4.10%" in markdown


def test_build_live_scorecard_handles_empty_universe_and_zero_shadow_runs():
    scorecard = build_live_scorecard(
        _diagnostics(
            universe_size=0,
            bars_200=0,
            fundamentals=0,
            earnings=0,
            chunks=0,
            macro_date=None,
            blocked_by={"data_gate": {}, "hard_veto": {}, "checklist": {}, "risk_math": {}, "passed": 0},
        ),
        fundamental_shadow={"run_count": 0, "outcome_counts": {}},
        technical_shadow={"run_count": 0, "decision_change_rate": 0.0},
        critic_shadow={"run_count": 0, "decision_change_rate": 0.0},
        technical_backtest={
            "horizon_trading_days": 20,
            "downgrade": {"sample_size": 0, "mean_forward_return": None},
            "neutral": {"sample_size": 0, "mean_forward_return": None},
            "excluded_insufficient_bars_count": 0,
        },
    )

    markdown = scorecard.as_markdown()
    assert "0/0 | 0.0%" in markdown
    assert "Latest macro date | none" in markdown
    assert f"`critic` | 0/{SHADOW_PROMOTION_TARGET_RUNS} | n/a | n/a | n/a" in markdown
    assert "`technical_would_downgrade` | 0 | n/a" in markdown
