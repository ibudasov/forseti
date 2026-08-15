from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
from sqlmodel import Session, select

from app.db.models import PriceBar, Security
from app.ingestion.prices import ingest_prices, to_price_bars


class TestPriceIngestion:
    def test_to_price_bars_flattens_columns_and_skips_nan_close(self):
        frame = pd.DataFrame(
            [
                [100.0, 101.0, 99.0, 100.5, 1000],
                [101.0, 102.0, 100.0, float("nan"), 1500],
                [102.0, 103.0, 101.0, 102.5, 2000],
            ],
            index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            columns=pd.MultiIndex.from_tuples(
                [
                    ("Open", "NVDA"),
                    ("High", "NVDA"),
                    ("Low", "NVDA"),
                    ("Close", "NVDA"),
                    ("Volume", "NVDA"),
                ]
            ),
        )

        bars = to_price_bars(7, frame)

        assert len(bars) == 2
        assert bars[0].bar_date == date(2026, 1, 1)
        assert bars[0].close == Decimal("100.5000")
        assert bars[1].bar_date == date(2026, 1, 3)
        assert bars[1].volume == 2000

    def test_ingest_prices_upserts_rows(self, db_engine, monkeypatch):
        security = Security(ticker="NVDA", name="NVIDIA Corporation", exchange="NASDAQ", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)

        frame = pd.DataFrame(
            [[100.0, 101.0, 99.0, 100.5, 1000]],
            index=pd.to_datetime(["2026-01-01"]),
            columns=["Open", "High", "Low", "Close", "Volume"],
        )

        monkeypatch.setattr("app.ingestion.prices.fetch_price_history", lambda _: frame)

        rows_upserted, failed_tickers = ingest_prices(engine=db_engine)

        assert rows_upserted == 1
        assert failed_tickers == []

        with Session(db_engine) as session:
            rows = session.exec(select(PriceBar).where(PriceBar.security_id == security.id)).all()
            assert len(rows) == 1
            assert rows[0].close == Decimal("100.5000")

    def test_ingest_prices_collects_failed_ticker_on_empty_frame(self, db_engine, monkeypatch):
        security = Security(ticker="FAIL1", name="Fail One", exchange="NASDAQ", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()

        monkeypatch.setattr("app.ingestion.prices.fetch_price_history", lambda _: pd.DataFrame())

        rows_upserted, failed_tickers = ingest_prices(engine=db_engine)

        assert rows_upserted == 0
        assert failed_tickers == ["FAIL1"]
