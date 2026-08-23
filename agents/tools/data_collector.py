"""Structured Data Collector tool: fetches OHLCV, indicators, fundamentals,
VIX, and earnings dates, and reports freshness/completeness warnings.

Wraps `app.services.ticker_profile.build_ticker_profile`. Deterministic,
no LLM involved.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from app.services.ticker_profile import build_ticker_profile

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

CollectStructuredData = Callable[[str], Dict[str, Any]]


def build_structured_data_collector_tool(
    engine: Optional["Engine"] = None,
    today: Optional[date] = None,
) -> CollectStructuredData:
    """Create a `collect_structured_data` tool bound to the given collaborators.

    The returned callable is what gets exposed to the agent/LLM: it only
    accepts a ticker symbol, keeping the database engine and clock as
    injected collaborators rather than global state.
    """

    def collect_structured_data(ticker: str) -> Dict[str, Any]:
        """Fetch the structured data profile (prices, indicators, fundamentals,
        earnings) for a validated ticker, including data freshness warnings.

        Returns `{"found": False}` when the ticker is unknown to the system.
        """
        profile = build_ticker_profile(ticker, engine=engine, today=today)
        if profile is None:
            return {"found": False, "ticker": ticker}
        # mode="json" keeps dates/Decimals serializable for the ADK function response.
        return {"found": True, **profile.model_dump(mode="json")}

    return collect_structured_data
