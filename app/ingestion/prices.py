from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

import pandas as pd
import yfinance as yf

from app.db.models import PriceBar
from app.db.repository import list_active_securities, upsert_price_bars
from app.settings import get_settings

logger = logging.getLogger(__name__)
_PRICE_QUANT = Decimal("0.0001")


def _flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame

    frame = frame.copy()
    frame.columns = [str(column[0]) if isinstance(column, tuple) else str(column) for column in frame.columns]
    return frame


def fetch_price_history(ticker: str) -> pd.DataFrame:
    settings = get_settings()
    return yf.download(
        ticker,
        period=settings.INGEST_PRICE_PERIOD,
        interval="1d",
        auto_adjust=False,
        progress=False,
    )


def to_price_bars(security_id: int, frame: pd.DataFrame) -> list[PriceBar]:
    if frame.empty:
        return []

    normalized_frame = _flatten_columns(frame)
    if "Close" not in normalized_frame.columns:
        return []

    filtered_frame = normalized_frame[normalized_frame["Close"].notna()]
    bars: list[PriceBar] = []
    for index, row in filtered_frame.iterrows():
        if pd.isna(row.get("Volume")):
            continue

        bars.append(
            PriceBar(
                security_id=security_id,
                bar_date=index.date(),
                open=Decimal(str(row["Open"])).quantize(_PRICE_QUANT),
                high=Decimal(str(row["High"])).quantize(_PRICE_QUANT),
                low=Decimal(str(row["Low"])).quantize(_PRICE_QUANT),
                close=Decimal(str(row["Close"])).quantize(_PRICE_QUANT),
                volume=int(row["Volume"]),
            )
        )
    return bars


def ingest_prices(engine=None, ticker: Optional[str] = None) -> tuple[int, list[str]]:
    active_securities = list_active_securities(engine=engine)
    if ticker is not None:
        normalized_ticker = ticker.strip().upper()
        active_securities = [security for security in active_securities if security.ticker == normalized_ticker]

    upserted_rows = 0
    failed_tickers: list[str] = []

    for security in active_securities:
        try:
            frame = fetch_price_history(security.ticker)
            bars = to_price_bars(security.id, frame)
            upsert_price_bars(bars, engine=engine)
            upserted_rows += len(bars)
            logger.info("prices_ingested: ticker=%s rows=%s", security.ticker, len(bars))
        except Exception:
            failed_tickers.append(security.ticker)
            logger.exception("prices_ingestion_failed: ticker=%s", security.ticker)

    return upserted_rows, failed_tickers
