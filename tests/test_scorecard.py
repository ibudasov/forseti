"""Tests for the product scorecard: `app/services/scorecard.py` and the
fixture/engine wiring in `scripts/scorecard.py`.

The unit tests below build synthetic `ScreeningResponse` objects directly —
no database — because `build_scorecard`/`compare` are pure functions of that
response. The single DB-backed test at the bottom seeds the committed
fixture universe and asserts the shape acceptance criteria require: a real
spread across all three decisions and a non-empty warning histogram.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.screening import ScreeningItem, ScreeningResponse
from app.services.scorecard import (
    MetricDirection,
    Scorecard,
    build_scorecard,
    compare,
)
from app.services.screening import run_screening

# Importing scripts.scorecard at module scope (rather than inside a test
# body) registers app.db.models on SQLModel.metadata *before* the db_engine
# fixture's drop_all/create_all runs, matching the convention every other
# DB-backed test file in this suite relies on.
from scripts.scorecard import seed_fixture

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "scorecard" / "universe.json"


def _screening_response(items, **overrides) -> ScreeningResponse:
    defaults = dict(
        generated_at=datetime.now(timezone.utc),
        engine_version="v1.rules.0",
        universe_size=len(items),
        analyzed_count=sum(1 for item in items if item.status == "ok"),
        failed_count=sum(1 for item in items if item.status == "error"),
        trade_count=sum(1 for item in items if item.decision == "trade"),
        watchlist_count=sum(1 for item in items if item.decision == "watchlist"),
        no_trade_count=sum(1 for item in items if item.decision == "no_trade"),
        items=items,
    )
    defaults.update(overrides)
    return ScreeningResponse(**defaults)


def _item(ticker: str, status="ok", decision=None, confidence=None, warnings=None, error=None) -> ScreeningItem:
    return ScreeningItem(
        ticker=ticker,
        sector_tag="ai",
        status=status,
        decision=decision,
        confidence=confidence,
        warnings=warnings or [],
        error=error,
    )


class TestBuildScorecard:
    def test_counts_are_copied_from_the_response(self):
        items = [
            _item("A", decision="trade", confidence=0.9),
            _item("B", decision="watchlist", confidence=0.6),
            _item("C", decision="no_trade", confidence=0.1),
            _item("D", status="error", error="boom"),
        ]
        response = _screening_response(items)

        scorecard = build_scorecard(response)

        assert scorecard.engine_version == "v1.rules.0"
        assert scorecard.universe_size == 4
        assert scorecard.analyzed_count == 3
        assert scorecard.failed_count == 1
        assert scorecard.trade_count == 1
        assert scorecard.watchlist_count == 1
        assert scorecard.no_trade_count == 1

    def test_zero_confidence_count_only_counts_ok_items_at_exactly_zero(self):
        items = [
            _item("A", decision="no_trade", confidence=0.0),
            _item("B", decision="watchlist", confidence=0.01),
            _item("C", status="error", error="boom"),  # confidence is None, must not count
        ]
        response = _screening_response(items)

        scorecard = build_scorecard(response)

        assert scorecard.zero_confidence_count == 1

    def test_warning_counts_are_aggregated_and_sorted_by_key(self):
        items = [
            _item("A", decision="no_trade", confidence=0.0, warnings=["no_earnings_data", "no_fundamentals"]),
            _item("B", decision="watchlist", confidence=0.5, warnings=["no_earnings_data"]),
        ]
        response = _screening_response(items)

        scorecard = build_scorecard(response)

        assert scorecard.warning_counts == {"no_earnings_data": 2, "no_fundamentals": 1}
        assert list(scorecard.warning_counts) == sorted(scorecard.warning_counts)

    def test_no_warnings_produces_empty_histogram(self):
        response = _screening_response([_item("A", decision="trade", confidence=1.0)])

        scorecard = build_scorecard(response)

        assert scorecard.warning_counts == {}


class TestScorecardSerialization:
    def _sample(self) -> Scorecard:
        response = _screening_response(
            [
                _item("A", decision="trade", confidence=1.0),
                _item("B", decision="no_trade", confidence=0.0, warnings=["no_earnings_data"]),
            ]
        )
        return build_scorecard(response)

    def test_as_json_is_deterministic_across_calls(self):
        scorecard = self._sample()

        assert scorecard.as_json() == scorecard.as_json()

    def test_as_json_excludes_wall_clock_time(self):
        payload = json.loads(self._sample().as_json())

        assert "generated_at" not in payload
        assert set(payload) == {
            "engine_version",
            "universe_size",
            "analyzed_count",
            "failed_count",
            "trade_count",
            "watchlist_count",
            "no_trade_count",
            "zero_confidence_count",
            "warning_counts",
        }

    def test_as_json_round_trips_all_core_fields(self):
        scorecard = self._sample()

        payload = json.loads(scorecard.as_json())

        assert payload["trade_count"] == scorecard.trade_count
        assert payload["zero_confidence_count"] == scorecard.zero_confidence_count
        assert payload["warning_counts"] == dict(scorecard.warning_counts)

    def test_as_markdown_contains_every_core_metric_and_every_warning(self):
        scorecard = self._sample()

        markdown = scorecard.as_markdown()

        for metric_name in (
            "universe_size",
            "analyzed_count",
            "failed_count",
            "trade_count",
            "watchlist_count",
            "no_trade_count",
            "zero_confidence_count",
        ):
            assert f"`{metric_name}`" in markdown
        assert "no_earnings_data" in markdown

    def test_as_markdown_has_no_raw_json_braces(self):
        markdown = self._sample().as_markdown()

        assert "{" not in markdown
        assert "}" not in markdown


class TestCompare:
    def _scorecard(self, **overrides) -> Scorecard:
        defaults = dict(
            engine_version="v1.rules.0",
            universe_size=6,
            analyzed_count=6,
            failed_count=0,
            trade_count=1,
            watchlist_count=3,
            no_trade_count=2,
            zero_confidence_count=3,
            warning_counts={"no_earnings_data": 4},
        )
        defaults.update(overrides)
        return Scorecard(**defaults)

    def test_unchanged_scorecard_has_no_regressions_and_no_improvements(self):
        baseline = self._scorecard()
        current = self._scorecard()

        delta = compare(baseline, current)

        assert not delta.has_regressions
        assert delta.regressions == ()
        assert delta.improvements == ()

    def test_more_failures_is_a_regression(self):
        baseline = self._scorecard(failed_count=0)
        current = self._scorecard(failed_count=1)

        delta = compare(baseline, current)

        assert delta.has_regressions
        names = [change.name for change in delta.regressions]
        assert "failed_count" in names

    def test_fewer_zero_confidence_items_is_an_improvement(self):
        baseline = self._scorecard(zero_confidence_count=3)
        current = self._scorecard(zero_confidence_count=1)

        delta = compare(baseline, current)

        assert not delta.has_regressions
        names = [change.name for change in delta.improvements]
        assert "zero_confidence_count" in names

    def test_more_of_a_warning_is_a_regression(self):
        baseline = self._scorecard(warning_counts={"no_earnings_data": 4})
        current = self._scorecard(warning_counts={"no_earnings_data": 6})

        delta = compare(baseline, current)

        assert delta.has_regressions
        assert any(change.name == "warning:no_earnings_data" for change in delta.regressions)

    def test_a_new_warning_key_absent_from_baseline_is_a_regression(self):
        baseline = self._scorecard(warning_counts={})
        current = self._scorecard(warning_counts={"stale_price_data": 2})

        delta = compare(baseline, current)

        assert delta.has_regressions
        assert any(change.name == "warning:stale_price_data" for change in delta.regressions)

    def test_trade_count_alone_never_regresses(self):
        """ibudasov/forseti#25 will legitimately reshuffle the trade/watchlist
        split once earnings data exists; a gate that fires here would block
        that work."""
        baseline = self._scorecard(trade_count=1, watchlist_count=3, no_trade_count=2)
        current = self._scorecard(trade_count=6, watchlist_count=0, no_trade_count=0)

        delta = compare(baseline, current)

        assert not delta.has_regressions

    def test_universe_size_and_analyzed_count_are_neutral(self):
        baseline = self._scorecard(universe_size=6, analyzed_count=6)
        current = self._scorecard(universe_size=10, analyzed_count=4)

        delta = compare(baseline, current)

        assert not delta.has_regressions
        assert not delta.improvements

    def test_failure_message_names_metric_baseline_and_current(self):
        baseline = self._scorecard(zero_confidence_count=1)
        current = self._scorecard(zero_confidence_count=3)

        delta = compare(baseline, current)
        regression = next(change for change in delta.regressions if change.name == "zero_confidence_count")

        message = regression.failure_message()

        assert "zero_confidence_count" in message
        assert "baseline=1" in message
        assert "current=3" in message


def test_metric_direction_enum_has_exactly_three_values():
    assert {direction.value for direction in MetricDirection} == {
        "lower_is_better",
        "higher_is_better",
        "neutral",
    }


class TestScorecardFixtureUniverse:
    """Runs the committed fixture through the real engine (needs Postgres)."""

    def test_fixture_produces_every_decision_and_a_warning_histogram(self, db_engine):
        fixture = json.loads(FIXTURE_PATH.read_text())

        today = seed_fixture(db_engine, fixture)
        response = run_screening(engine=db_engine, today=today)
        scorecard = build_scorecard(response)

        assert scorecard.trade_count >= 1
        assert scorecard.watchlist_count >= 1
        assert scorecard.no_trade_count >= 1
        assert scorecard.failed_count == 0
        assert scorecard.warning_counts

    def test_running_the_fixture_twice_is_byte_identical(self, db_engine):
        fixture = json.loads(FIXTURE_PATH.read_text())

        today = seed_fixture(db_engine, fixture)
        first = build_scorecard(run_screening(engine=db_engine, today=today)).as_json()
        second = build_scorecard(run_screening(engine=db_engine, today=today)).as_json()

        assert first == second
