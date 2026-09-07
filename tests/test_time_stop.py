"""Tests for the deterministic XNYS time-stop policy."""
from datetime import date

from app.services.time_stop import calculate_time_stop_at


def test_counts_sessions_strictly_after_a_weekday():
    assert calculate_time_stop_at(date(2026, 1, 2)) == date(2026, 3, 3)


def test_skips_weekends_and_full_day_holidays():
    assert calculate_time_stop_at(date(2026, 1, 1)) == date(2026, 3, 2)
    assert calculate_time_stop_at(date(2026, 1, 3)) == date(2026, 3, 3)


def test_early_close_session_counts():
    assert calculate_time_stop_at(date(2026, 7, 1)) == date(2026, 8, 27)


def test_returns_a_plain_date_deterministically():
    result = calculate_time_stop_at(date(2026, 11, 26))

    assert result == date(2027, 1, 26)
    assert type(result) is date
    assert calculate_time_stop_at(date(2026, 11, 26)) == result
