from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date
from typing import Optional

import httpx

from app.db.models import EarningsEvent
from app.db.repository import list_active_securities, upsert_earnings_events
from app.settings import get_settings

logger = logging.getLogger(__name__)
_EARNINGS_CALENDAR_URL = "https://www.alphavantage.co/query"

MISSING_API_KEY_MARKER = "earnings_missing_api_key"
SOURCE_FAILURE_MARKER = "earnings_source"

_REQUIRED_CSV_COLUMN = "symbol"
_REFUSAL_KEYS = ("Error Message", "Information", "Note")
_PLACEHOLDER_API_KEYS = frozenset(
    {"", "changeme", "demo", "none", "null", "todo", "your_api_key", "your-api-key"}
)


class EarningsSourceError(RuntimeError):
    """Alpha Vantage responded, but not with a usable earnings calendar."""


def normalize_api_key(raw_api_key: Optional[str]) -> Optional[str]:
    if raw_api_key is None:
        return None

    normalized_api_key = raw_api_key.strip()
    if normalized_api_key.lower() in _PLACEHOLDER_API_KEYS:
        return None

    return normalized_api_key


def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 4:
        return "*" * len(api_key)
    return f"{api_key[:2]}***{api_key[-2:]}"


def _refusal_reason(payload_text: str) -> Optional[str]:
    try:
        payload = json.loads(payload_text)
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None

    for key in _REFUSAL_KEYS:
        if payload.get(key):
            return f"{key}: {payload[key]}"

    return None


def _first_meaningful_line(payload_text: str) -> str:
    for line in payload_text.splitlines():
        stripped_line = line.strip()
        if stripped_line:
            return stripped_line

    return ""


def validate_earnings_payload(payload_text: str) -> str:
    refusal_reason = _refusal_reason(payload_text)
    if refusal_reason is not None:
        raise EarningsSourceError(
            f"Alpha Vantage refusal: {refusal_reason}. function=EARNINGS_CALENDAR is a premium endpoint "
            "that a free-tier API key cannot read."
        )

    first_line = _first_meaningful_line(payload_text)
    if _REQUIRED_CSV_COLUMN not in first_line.split(","):
        preview = first_line[:120]
        raise EarningsSourceError(
            f"Unexpected earnings calendar payload format. Missing required CSV column '{_REQUIRED_CSV_COLUMN}' "
            f"in first line: {preview!r}"
        )

    return payload_text


def fetch_earnings_calendar_csv(api_key: str) -> str:
    response = httpx.get(
        _EARNINGS_CALENDAR_URL,
        params={"function": "EARNINGS_CALENDAR", "horizon": "12month", "apikey": api_key},
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


def _active_tickers(engine=None, ticker: Optional[str] = None) -> dict[str, int]:
    active_securities = list_active_securities(engine=engine)
    if ticker is not None:
        normalized_ticker = ticker.strip().upper()
        active_securities = [security for security in active_securities if security.ticker == normalized_ticker]

    return {security.ticker: security.id for security in active_securities}


def ingest_earnings(engine=None, ticker: Optional[str] = None) -> tuple[int, list[str]]:
    api_key = normalize_api_key(get_settings().ALPHA_VANTAGE_API_KEY)
    if api_key is None:
        logger.error(
            "earnings_ingestion_failed: marker=%s message=Set ALPHA_VANTAGE_API_KEY in .env",
            MISSING_API_KEY_MARKER,
        )
        return 0, [MISSING_API_KEY_MARKER]

    active_tickers = _active_tickers(engine=engine, ticker=ticker)

    try:
        events = parse_earnings_calendar(
            validate_earnings_payload(fetch_earnings_calendar_csv(api_key)),
            active_tickers,
        )
    except Exception:
        logger.exception("earnings_ingestion_failed: api_key=%s", mask_api_key(api_key))
        return 0, [SOURCE_FAILURE_MARKER]

    if not events:
        logger.error(
            "earnings_ingestion_failed: reason=no_rows_matched active_tickers=%s api_key=%s",
            len(active_tickers),
            mask_api_key(api_key),
        )
        return 0, [SOURCE_FAILURE_MARKER]

    upsert_earnings_events(events, engine=engine)
    logger.info("earnings_ingested: rows=%s api_key=%s", len(events), mask_api_key(api_key))
    return len(events), []
