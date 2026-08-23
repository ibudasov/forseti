from __future__ import annotations

import logging
from decimal import Decimal

import pandas as pd
import yfinance as yf

from app.db.models import MacroDaily
from app.db.repository import upsert_macro_daily_rows
from app.settings import get_settings

logger = logging.getLogger(__name__)
_VIX_QUANT = Decimal("0.001")
_VIX_TICKER = "^VIX"


def _flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame

    frame = frame.copy()
    flattened_columns: list[str] = []
    for column in frame.columns:
        if not isinstance(column, tuple):
            flattened_columns.append(str(column))
            continue

        labels = [str(label) for label in column if label is not None]
        if any(label == "Close" for label in labels):
            flattened_columns.append("Close")
            continue
        if any(label == "Open" for label in labels):
            flattened_columns.append("Open")
            continue

        flattened_columns.append(str(labels[-1]) if labels else str(column))

    frame.columns = flattened_columns
    return frame


def fetch_vix_history() -> pd.DataFrame:
    settings = get_settings()
    return yf.download(
        _VIX_TICKER,
        period=settings.INGEST_PRICE_PERIOD,
        interval="1d",
        auto_adjust=False,
        progress=False,
    )


def to_macro_rows(frame: pd.DataFrame) -> list[MacroDaily]:
    if frame.empty:
        return []

    normalized_frame = _flatten_columns(frame)
    if "Close" not in normalized_frame.columns:
        return []

    filtered_frame = normalized_frame[normalized_frame["Close"].notna()]
    rows: list[MacroDaily] = []
    for index, row in filtered_frame.iterrows():
        rows.append(
            MacroDaily(
                obs_date=index.date(),
                vix=Decimal(str(row["Close"])).quantize(_VIX_QUANT),
            )
        )
    return rows


def ingest_vix(engine=None) -> int:
    frame = fetch_vix_history()
    if frame.empty:
        raise ValueError("empty VIX frame")
    rows = to_macro_rows(frame)
    if not rows:
        raise ValueError("no valid VIX rows after mapping")
    upsert_macro_daily_rows(rows, engine=engine)
    logger.info("vix_ingested: rows=%s", len(rows))
    return len(rows)
