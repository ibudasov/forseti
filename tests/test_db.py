from __future__ import annotations

from datetime import date
from decimal import Decimal
import os

import pytest
from sqlalchemy import inspect
from sqlmodel import SQLModel, Session, create_engine, select
from testcontainers.community.postgres import PostgresContainer

from app.db.models import (
    EarningsEvent,
    MacroDaily,
    PriceBar,
    Recommendation,
    Security,
)
from app.db.repository import (
    get_latest_bars,
    save_recommendation,
    upsert_earnings_event,
    upsert_macro_daily,
    upsert_price_bars,
)


def _create_test_engine(database_url: str):
    return create_engine(database_url, echo=False, future=True)


@pytest.fixture
def db_engine():
    # Prefer an explicitly provided database URL (useful in docker-compose test runs).
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        engine = _create_test_engine(url)
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        yield engine
        engine.dispose()
        return

    with PostgresContainer("postgres:15") as postgres:
        url = postgres.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        engine = _create_test_engine(url)
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        yield engine
        engine.dispose()


def test_tables_exist(db_engine):
    inspector = inspect(db_engine)
    assert inspector.has_table("security")
    assert inspector.has_table("price_bar")
    assert inspector.has_table("fundamental")
    assert inspector.has_table("earnings_event")
    assert inspector.has_table("macro_daily")
    assert inspector.has_table("technical_feature")
    assert inspector.has_table("recommendation")


def test_idempotent_price_bar_upsert(db_engine):
    security = Security(ticker="NVDA", name="NVIDIA Corporation", exchange="NASDAQ", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    bar = PriceBar(
        security_id=security.id,
        bar_date=date(2026, 1, 1),
        open=Decimal("100.0000"),
        high=Decimal("110.0000"),
        low=Decimal("99.5000"),
        close=Decimal("108.2500"),
        volume=1_000_000,
    )

    upsert_price_bars([bar], engine=db_engine)
    upsert_price_bars([bar], engine=db_engine)

    with Session(db_engine) as session:
        rows = session.exec(select(PriceBar).where(PriceBar.security_id == security.id)).all()
        assert len(rows) == 1
        assert rows[0].close == Decimal("108.2500")


def test_idempotent_earnings_event_upsert(db_engine):
    security = Security(ticker="AAPL", name="Apple Inc.", exchange="NASDAQ", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    event = EarningsEvent(security_id=security.id, report_date=date(2026, 2, 1), confirmed=True)
    upsert_earnings_event(event, engine=db_engine)
    upsert_earnings_event(event, engine=db_engine)

    with Session(db_engine) as session:
        rows = session.exec(select(EarningsEvent).where(EarningsEvent.security_id == security.id)).all()
        assert len(rows) == 1
        assert rows[0].confirmed is True


def test_idempotent_macro_daily_upsert(db_engine):
    row = MacroDaily(obs_date=date(2026, 3, 1), vix=Decimal("20.123"))
    upsert_macro_daily(row, engine=db_engine)
    upsert_macro_daily(row, engine=db_engine)

    with Session(db_engine) as session:
        rows = session.exec(select(MacroDaily).where(MacroDaily.obs_date == date(2026, 3, 1))).all()
        assert len(rows) == 1
        assert rows[0].vix == Decimal("20.123")


def test_save_recommendation_is_append_only(db_engine):
    security = Security(ticker="MSFT", name="Microsoft Corporation", exchange="NASDAQ", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    recommendation = Recommendation(
        security_id=security.id,
        decision="trade",
        entry_low=Decimal("250.0000"),
        entry_high=Decimal("260.0000"),
        stop_loss=Decimal("245.0000"),
        take_profit_1=Decimal("270.0000"),
        take_profit_2=Decimal("280.0000"),
        risk_reward=Decimal("2.0000"),
        position_size=Decimal("9999.0000"),
        confidence=Decimal("0.850"),
        reasons=["momentum breakout"],
        warnings=["high beta"],
        full_payload={"decision": "trade"},
        engine_version="v1.0.0",
    )

    saved = save_recommendation(recommendation, engine=db_engine)
    assert saved.id is not None
    assert saved.security_id == security.id

    with pytest.raises(ValueError):
        save_recommendation(saved, engine=db_engine)


def test_get_latest_bars_returns_expected_rows(db_engine):
    security = Security(ticker="GOOG", name="Alphabet Inc.", exchange="NASDAQ", sector_tag="ai")

    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    bars = [
        PriceBar(
            security_id=security.id,
            bar_date=date(2026, 1, 1),
            open=Decimal("100.0000"),
            high=Decimal("105.0000"),
            low=Decimal("99.0000"),
            close=Decimal("104.0000"),
            volume=500_000,
        ),
        PriceBar(
            security_id=security.id,
            bar_date=date(2026, 1, 2),
            open=Decimal("104.0000"),
            high=Decimal("108.0000"),
            low=Decimal("103.0000"),
            close=Decimal("107.5000"),
            volume=600_000,
        ),
    ]
    upsert_price_bars(bars, engine=db_engine)

    latest_bars = get_latest_bars("goog", 2, engine=db_engine)
    assert len(latest_bars) == 2
    assert latest_bars[0].bar_date == date(2026, 1, 2)
