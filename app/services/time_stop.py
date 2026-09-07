"""Deterministic trading-session time-stop policy."""
from __future__ import annotations

from datetime import date

import exchange_calendars as xcals
import pandas as pd


TIME_STOP_TRADING_SESSIONS = 40
XNYS_CALENDAR_NAME = "XNYS"
_XNYS_CALENDAR = xcals.get_calendar(XNYS_CALENDAR_NAME)


class TimeStopCalculationError(RuntimeError):
    """Raised when the exchange calendar cannot provide the time-stop window."""


def calculate_time_stop_at(recommendation_date: date) -> date:
    """Return the 40th XNYS session strictly after recommendation_date."""
    sessions_after_recommendation = _XNYS_CALENDAR.sessions[
        _XNYS_CALENDAR.sessions > pd.Timestamp(recommendation_date)
    ]
    if len(sessions_after_recommendation) < TIME_STOP_TRADING_SESSIONS:
        raise TimeStopCalculationError(
            f"XNYS calendar has fewer than {TIME_STOP_TRADING_SESSIONS} sessions "
            f"after {recommendation_date.isoformat()}."
        )

    return sessions_after_recommendation[TIME_STOP_TRADING_SESSIONS - 1].date()
