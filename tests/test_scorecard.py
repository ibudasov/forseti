"""Tests for `app.services.scorecard`.

Most tests build synthetic `ScreeningResponse` objects directly, exercising
`build_scorecard` and `compare` as pure functions with no database. A smaller
set of integration tests seeds the committed fixture universe into a real
database and runs the full screening pipeline, verifying the fixture spans
every decision bucket the way the scorecard's usefulness depends on.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from app.schemas.screening import ScreeningItem, ScreeningResponse
from app.services.scorecard import (
    MetricDirection,
    build_scorecard,
    compare,
    metric_direction,
)
from scripts.scorecard import load_fixture, seed_fixture

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "scorecard" / "universe.json"


def _item(
    ticker: str,
    status: str = "ok",
    decision: str | None = "trade",
    confidence: float | None = 0.8,
    warnings: list[str] | None = None,
) -> ScreeningItem:
    return ScreeningItem(
        ticker=ticker,
        sector_tag="ai",
        status=status,
        decision=decision,
        confidence=confidence,
        warnings=warnings or [],
    )


def _response(items: list[ScreeningItem], **overrides) -> ScreeningResponse:
    trade_count = sum(1 for i in items if i.status == "ok" and i.decision == "trade")
    watchlist_count = sum(1 for i in items if i.status == "ok" and i.decision == "watchlist")
    no_trade_count = sum(1 for i in items if i.status == "ok" and i.decision == "no_trade")
    failed_count = sum(1 for i in items if i.status == "error")
    defaults = dict(
        generated_at=datetime.now(timezone.utc),
        engine_version="v1.rules.0",
        universe_size=len(items),
        analyzed_count=len(items) - failed_count,
        failed_count=failed_count,
        trade_count=trade_count,
        watchlist_count=watchlist_count,
        no_trade_count=no_trade_count,
        items=items,
    )
    defaults.update(overrides)
    return ScreeningResponse(**defaults)


class TestBuildScorecard:
    def test_counts_carry_over_from_the_response(self):
        response = _response(
            [
                _item("AAA", decision="trade"),
                _item("BBB", decision="watchlist"),
                _item("CCC", decision="no_trade"),
            ]
        )

        scorecard = build_scorecard(response)

        assert scorecard.universe_size == 3
        assert scorecard.trade_count == 1
        assert scorecard.watchlist_count == 1
        assert scorecard.no_trade_count == 1
        assert scorecard.failed_count == 0
        assert scorecard.analyzed_count == 3
        assert scorecard.engine_version == "v1.rules.0"

    def test_zero_confidence_count_only_counts_ok_items_at_exactly_zero(self):
        response = _response(
            [
                _item("AAA", confidence=0.0),
                _item("BBB", confidence=0.01),
                _item("CCC", status="error", decision=None, confidence=None),
            ]
        )

        scorecard = build_scorecard(response)

        assert scorecard.zero_confidence_count == 1

    def test_warning_counts_are_tallied_across_items_and_sorted(self):
        response = _response(
            [
                _item("AAA", warnings=["no_earnings_data", "stale_price_data"]),
                _item("BBB", warnings=["no_earnings_data"]),
            ]
        )

        scorecard = build_scorecard(response)

        assert scorecard.warning_counts == {"no_earnings_data": 2, "stale_price_data": 1}
        assert list(scorecard.warning_counts) == sorted(scorecard.warning_counts)


class TestScorecardSerialization:
    def test_as_json_excludes_wall_clock_time(self):
        response = _response([_item("AAA")])
        scorecard = build_scorecard(response)

        assert "generated_at" not in scorecard.as_json()

    def test_as_json_is_byte_identical_across_calls(self):
        response = _response([_item("AAA"), _item("BBB", decision="watchlist")])
        scorecard = build_scorecard(response)

        assert scorecard.as_json() == scorecard.as_json()

    def test_as_json_round_trips_every_metric(self):
        response = _response(
            [_item("AAA", decision="trade"), _item("BBB", decision="no_trade", confidence=0.0)]
        )
        scorecard = build_scorecard(response)

        payload = json.loads(scorecard.as_json())

        assert payload["trade_count"] == 1
        assert payload["no_trade_count"] == 1
        assert payload["zero_confidence_count"] == 1

    def test_as_markdown_renders_a_table_with_every_metric_and_warning(self):
        response = _response([_item("AAA", warnings=["no_earnings_data"])])
        scorecard = build_scorecard(response)

        markdown = scorecard.as_markdown()

        assert markdown.startswith("| Metric | Value |")
        for metric_name in (
            "universe_size",
            "analyzed_count",
            "failed_count",
            "trade_count",
            "watchlist_count",
            "no_trade_count",
            "zero_confidence_count",
        ):
            assert f"| {metric_name} |" in markdown
        assert "| warning: no_earnings_data | 1 |" in markdown


class TestMetricDirection:
    def test_diagnostic_metrics_are_lower_is_better(self):
        assert metric_direction("failed_count") == MetricDirection.LOWER_IS_BETTER
        assert metric_direction("zero_confidence_count") == MetricDirection.LOWER_IS_BETTER

    def test_decision_split_metrics_are_neutral(self):
        assert metric_direction("trade_count") == MetricDirection.NEUTRAL
        assert metric_direction("watchlist_count") == MetricDirection.NEUTRAL
        assert metric_direction("no_trade_count") == MetricDirection.NEUTRAL
        assert metric_direction("universe_size") == MetricDirection.NEUTRAL

    def test_any_warning_key_is_lower_is_better(self):
        assert metric_direction("warning:no_earnings_data") == MetricDirection.LOWER_IS_BETTER
        assert metric_direction("warning:anything_not_seen_before") == MetricDirection.LOWER_IS_BETTER


class TestCompare:
    def test_raising_zero_confidence_count_is_a_regression(self):
        baseline = build_scorecard(_response([_item("AAA", confidence=0.5)]))
        current = build_scorecard(_response([_item("AAA", confidence=0.0)]))

        delta = compare(baseline, current)

        assert delta.has_regressions
        regressed_metrics = {change.metric for change in delta.regressions}
        assert "zero_confidence_count" in regressed_metrics

    def test_changing_trade_count_alone_is_not_a_regression(self):
        baseline = build_scorecard(_response([_item("AAA", decision="trade")]))
        current = build_scorecard(
            _response([_item("AAA", decision="watchlist"), _item("BBB", decision="trade")])
        )

        delta = compare(baseline, current)

        assert not delta.has_regressions

    def test_a_new_warning_type_appearing_is_a_regression(self):
        baseline = build_scorecard(_response([_item("AAA", warnings=[])]))
        current = build_scorecard(_response([_item("AAA", warnings=["no_fundamentals"])]))

        delta = compare(baseline, current)

        assert delta.has_regressions
        assert any(change.metric == "warning:no_fundamentals" for change in delta.regressions)

    def test_lowering_failed_count_is_an_improvement(self):
        baseline = build_scorecard(_response([_item("AAA", status="error", decision=None, confidence=None)]))
        current = build_scorecard(_response([_item("AAA", status="ok")]))

        delta = compare(baseline, current)

        assert not delta.has_regressions
        assert any(change.metric == "failed_count" for change in delta.improvements)

    def test_regression_report_names_metric_and_both_values(self):
        baseline = build_scorecard(_response([_item("AAA", confidence=0.5)]))
        current = build_scorecard(_response([_item("AAA", confidence=0.0)]))

        delta = compare(baseline, current)

        report = delta.regression_report()
        assert "zero_confidence_count" in report
        assert "baseline=0" in report
        assert "current=1" in report


class TestFixtureUniverse:
    """The committed fixture must span every decision bucket, or the
    scorecard collapses into a useless single-bucket signal."""

    def test_fixture_produces_trade_watchlist_and_no_trade_items(self, db_engine):
        fixture = load_fixture(FIXTURE_PATH)
        seed_fixture(db_engine, fixture)

        from app.services.screening import run_screening

        response = run_screening(engine=db_engine, today=date.fromisoformat(fixture["today"]))

        assert response.trade_count >= 1
        assert response.watchlist_count >= 1
        assert response.no_trade_count >= 1
        assert response.failed_count == 0

    def test_fixture_produces_a_non_empty_warning_histogram(self, db_engine):
        fixture = load_fixture(FIXTURE_PATH)
        seed_fixture(db_engine, fixture)

        from app.services.screening import run_screening

        response = run_screening(engine=db_engine, today=date.fromisoformat(fixture["today"]))
        scorecard = build_scorecard(response)

        assert scorecard.warning_counts

    def test_running_the_fixture_twice_is_byte_identical(self, db_engine):
        fixture = load_fixture(FIXTURE_PATH)
        seed_fixture(db_engine, fixture)
        today = date.fromisoformat(fixture["today"])

        from app.services.screening import run_screening

        first = build_scorecard(run_screening(engine=db_engine, today=today)).as_json()
        second = build_scorecard(run_screening(engine=db_engine, today=today)).as_json()

        assert first == second
