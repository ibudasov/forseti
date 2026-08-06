from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Optional

import httpx

from app.db.models import EarningsEvent
from app.db.repository import list_active_securities, upsert_earnings_events
from app.settings import get_settings

logger = logging.getLogger(__name__)
_EARNINGS_CALENDAR_URL = "https://www.alphavantage.co/query"


def fetch_earnings_calendar_csv(api_key: str) -> str:
    response = httpx.get(
        _EARNINGS_CALENDAR_URL,
        params={"function": "EARNINGS_CALENDAR", "horizon": "3month", "apikey": api_key},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.text


def parse_earnings_calendar(csv_text: str, active_tickers: dict[str, int]) -> list[EarningsEvent]:
    rows = csv.DictReader(io.StringIO(csv_text))
    events: list[EarningsEvent] = []

    for row in rows:
        ticker = str(row.get("symbol", "")).strip().upper()
        if ticker not in active_tickers:
            continue

        report_date_raw = str(row.get("reportDate", "")).strip()
        if not report_date_raw:
            continue

        # Alpha Vantage calendar is an estimate feed, not confirmed event reporting.
        events.append(
            EarningsEvent(
                security_id=active_tickers[ticker],
                report_date=date.fromisoformat(report_date_raw),
                confirmed=False,
            )
        )

    return events


def ingest_earnings(engine=None, ticker: Optional[str] = None) -> tuple[int, list[str]]:
    settings = get_settings()
    if not settings.ALPHA_VANTAGE_API_KEY:
        logger.warning("earnings_skipped: ALPHA_VANTAGE_API_KEY is not set")
        return 0, []

    active_securities = list_active_securities(engine=engine)
    if ticker is not None:
        normalized_ticker = ticker.strip().upper()
        active_securities = [security for security in active_securities if security.ticker == normalized_ticker]

    active_tickers = {security.ticker: security.id for security in active_securities}

    try:
        csv_payload = fetch_earnings_calendar_csv(settings.ALPHA_VANTAGE_API_KEY)
        events = parse_earnings_calendar(csv_payload, active_tickers)
        upsert_earnings_events(events, engine=engine)
        logger.info("earnings_ingested: rows=%s", len(events))
        return len(events), []
    except Exception:
        logger.exception("earnings_ingestion_failed")
        return 0, ["earnings_source"]
