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
    get_latest_fundamental,
    get_latest_technical_feature,
    get_next_earnings_event,
    list_active_securities,
    count_price_bars,
    get_latest_bars,
    save_recommendation,
    upsert_fundamental,
    upsert_earnings_event,
    upsert_earnings_events,
    upsert_macro_daily,
    upsert_macro_daily_rows,
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


def test_earnings_event_upsert_updates_existing_row(db_engine):
    security = Security(ticker="EUPD", name="Earnings Update", exchange="NASDAQ", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    upsert_earnings_events(
        [EarningsEvent(security_id=security.id, report_date=date(2026, 2, 1), confirmed=False)],
        engine=db_engine,
    )
    upsert_earnings_events(
        [EarningsEvent(security_id=security.id, report_date=date(2026, 2, 1), confirmed=True)],
        engine=db_engine,
    )

    with Session(db_engine) as session:
        row = session.exec(
            select(EarningsEvent).where(EarningsEvent.security_id == security.id)
        ).one()
        assert row.confirmed is True


def test_idempotent_macro_daily_upsert(db_engine):
    row = MacroDaily(obs_date=date(2026, 3, 1), vix=Decimal("20.123"))
    upsert_macro_daily(row, engine=db_engine)
    upsert_macro_daily(row, engine=db_engine)

    with Session(db_engine) as session:
        rows = session.exec(select(MacroDaily).where(MacroDaily.obs_date == date(2026, 3, 1))).all()
        assert len(rows) == 1
        assert rows[0].vix == Decimal("20.123")


def test_macro_daily_upsert_updates_existing_row(db_engine):
    upsert_macro_daily_rows([MacroDaily(obs_date=date(2026, 3, 2), vix=Decimal("21.000"))], engine=db_engine)
    upsert_macro_daily_rows([MacroDaily(obs_date=date(2026, 3, 2), vix=Decimal("19.100"))], engine=db_engine)

    with Session(db_engine) as session:
        row = session.exec(select(MacroDaily).where(MacroDaily.obs_date == date(2026, 3, 2))).one()
        assert row.vix == Decimal("19.100")


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


from app.db.models import Fundamental, TechnicalFeature


def test_count_price_bars(db_engine):
    security = Security(ticker="CPBR", name="Count PriceBar", exchange="NYSE", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    bars = [
        PriceBar(security_id=security.id, bar_date=date(2026, 1, 1), open=Decimal("10"), high=Decimal("11"), low=Decimal("9"), close=Decimal("10"), volume=100),
        PriceBar(security_id=security.id, bar_date=date(2026, 1, 2), open=Decimal("10"), high=Decimal("11"), low=Decimal("9"), close=Decimal("10"), volume=100),
    ]
    upsert_price_bars(bars, engine=db_engine)

    assert count_price_bars("CPBR", engine=db_engine) == 2
    assert count_price_bars("UNKNOWN_XYZ", engine=db_engine) == 0


def test_get_latest_technical_feature_returns_latest(db_engine):
    security = Security(ticker="TECH1", name="Tech One", exchange="NASDAQ", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)
        session.add_all([
            TechnicalFeature(security_id=security.id, as_of_date=date(2026, 1, 1), rsi_14=Decimal("45.0")),
            TechnicalFeature(security_id=security.id, as_of_date=date(2026, 1, 2), rsi_14=Decimal("58.0")),
        ])
        session.commit()

    result = get_latest_technical_feature("TECH1", engine=db_engine)
    assert result is not None
    assert result.as_of_date == date(2026, 1, 2)
    assert result.rsi_14 == Decimal("58.0")


def test_get_latest_fundamental_returns_latest(db_engine):
    security = Security(ticker="FUND1", name="Fund One", exchange="NYSE", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)
        session.add_all([
            Fundamental(security_id=security.id, as_of_date=date(2025, 12, 31), revenue_growth=Decimal("0.3"), raw_payload={}),
            Fundamental(security_id=security.id, as_of_date=date(2026, 3, 31), revenue_growth=Decimal("0.5"), raw_payload={}),
        ])
        session.commit()

    result = get_latest_fundamental("FUND1", engine=db_engine)
    assert result is not None
    assert result.as_of_date == date(2026, 3, 31)
    assert result.revenue_growth == Decimal("0.5")


def test_get_next_earnings_event_skips_past(db_engine):
    security = Security(ticker="EARN1", name="Earnings One", exchange="NYSE", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)
        session.add_all([
            EarningsEvent(security_id=security.id, report_date=date(2025, 10, 1), confirmed=True),
            EarningsEvent(security_id=security.id, report_date=date(2026, 4, 1), confirmed=False),
        ])
        session.commit()

    today = date(2026, 1, 1)
    result = get_next_earnings_event("EARN1", on_or_after=today, engine=db_engine)
    assert result is not None
    assert result.report_date == date(2026, 4, 1)

    past_only = get_next_earnings_event("EARN1", on_or_after=date(2027, 1, 1), engine=db_engine)
    assert past_only is None


def test_upsert_fundamental_updates_matching_security_and_date(db_engine):
    security = Security(ticker="FUPD", name="Fund Update", exchange="NYSE", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    initial = Fundamental(
        security_id=security.id,
        as_of_date=date(2025, 12, 31),
        revenue_growth=Decimal("0.100000"),
        raw_payload={"version": 1},
    )
    updated = Fundamental(
        security_id=security.id,
        as_of_date=date(2025, 12, 31),
        revenue_growth=Decimal("0.250000"),
        fcf=Decimal("1000.0000"),
        raw_payload={"version": 2},
    )

    upsert_fundamental(initial, engine=db_engine)
    upsert_fundamental(updated, engine=db_engine)

    with Session(db_engine) as session:
        rows = session.exec(select(Fundamental).where(Fundamental.security_id == security.id)).all()
        assert len(rows) == 1
        assert rows[0].revenue_growth == Decimal("0.250000")
        assert rows[0].fcf == Decimal("1000.0000")
        assert rows[0].raw_payload == {"version": 2}


def test_list_active_securities_filters_inactive_rows(db_engine):
    with Session(db_engine) as session:
        session.add(Security(ticker="ACT1", name="Active One", exchange="NASDAQ", sector_tag="ai", is_active=True))
        session.add(Security(ticker="INACT1", name="Inactive One", exchange="NYSE", sector_tag="ai", is_active=False))
        session.commit()

    active = list_active_securities(engine=db_engine)
    active_tickers = [security.ticker for security in active]
    assert "ACT1" in active_tickers
    assert "INACT1" not in active_tickers
