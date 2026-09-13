from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlmodel import Session, select

from app.db.models import (
    EarningsEvent,
    MacroDaily,
    PriceBar,
    Recommendation,
    Security,
    Fundamental,
    FundamentalObservation,
    TechnicalFeature,
)
from app.db.repository import (
    count_fundamental_observations,
    get_latest_fundamental,
    get_latest_authoritative_observation,
    get_latest_macro_daily,
    get_latest_technical_feature,
    get_next_earnings_event,
    count_price_bars,
    get_latest_bars,
    list_active_securities,
    list_fundamental_observations,
    save_recommendation,
    upsert_fundamental,
    upsert_fundamental_observations,
    upsert_earnings_event,
    upsert_earnings_events,
    upsert_macro_daily,
    upsert_macro_daily_rows,
    upsert_price_bars,
    upsert_technical_feature,
)
from app.services.fundamental_history import build_fundamental_history


def test_tables_exist(db_engine):
    inspector = inspect(db_engine)
    assert inspector.has_table("security")
    assert inspector.has_table("price_bar")
    assert inspector.has_table("fundamental")
    assert inspector.has_table("fundamental_observation")
    assert inspector.has_table("earnings_event")
    assert inspector.has_table("macro_daily")
    assert inspector.has_table("technical_feature")
    assert inspector.has_table("recommendation")
    assert inspector.has_table("document_chunk")


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


def test_count_price_bars(db_engine):
    security = Security(ticker="CPBR", name="Count PriceBar", exchange="NYSE", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    bars = [
        PriceBar(
            security_id=security.id, bar_date=date(2026, 1, 1), open=Decimal("10"),
            high=Decimal("11"), low=Decimal("9"), close=Decimal("10"), volume=100,
        ),
        PriceBar(
            security_id=security.id, bar_date=date(2026, 1, 2), open=Decimal("10"),
            high=Decimal("11"), low=Decimal("9"), close=Decimal("10"), volume=100,
        ),
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


def test_fundamental_observation_upsert_and_authoritative_reads(db_engine):
    security = Security(ticker="OBS1", name="Observation One", exchange="NYSE", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    observations = [
        FundamentalObservation(
            security_id=security.id,
            metric_name="revenue",
            value=Decimal("250.000000"),
            unit="USD",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 6, 30),
            fiscal_year=2025,
            fiscal_period="Q2",
            form_type="10-Q",
            filed_at=date(2025, 7, 20),
            accession_number="0002",
            source_concept="Revenues",
            source_url="sec://revenues",
            is_derived=False,
        ),
        FundamentalObservation(
            security_id=security.id,
            metric_name="revenue",
            value=Decimal("260.000000"),
            unit="USD",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 6, 30),
            fiscal_year=2025,
            fiscal_period="Q2",
            form_type="10-Q/A",
            filed_at=date(2025, 8, 1),
            accession_number="0002A",
            source_concept="Revenues",
            source_url="sec://revenues-amended",
            is_derived=False,
        ),
        FundamentalObservation(
            security_id=security.id,
            metric_name="revenue",
            value=Decimal("700.000000"),
            unit="USD",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            fiscal_year=2025,
            fiscal_period="FY",
            form_type="10-K",
            filed_at=date(2026, 2, 10),
            accession_number="0004",
            source_concept="Revenues",
            source_url="sec://revenues-fy",
            is_derived=False,
        ),
        FundamentalObservation(
            security_id=security.id,
            metric_name="net_margin",
            value=Decimal("0.200000"),
            unit="ratio",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            fiscal_year=2025,
            fiscal_period="FY",
            form_type="10-K",
            filed_at=date(2026, 2, 10),
            accession_number="0004",
            source_concept="derived:net_margin",
            source_url="sec://revenues-fy",
            is_derived=True,
            derivation="net_margin derived from test rows",
        ),
    ]

    upsert_fundamental_observations(observations, engine=db_engine)
    upsert_fundamental_observations(observations, engine=db_engine)

    assert count_fundamental_observations("OBS1", engine=db_engine) == 4

    all_q2_rows = list_fundamental_observations(
        "obs1",
        metric_name="revenue",
        fiscal_period="Q2",
        engine=db_engine,
    )
    authoritative_q2_rows = list_fundamental_observations(
        "OBS1",
        metric_name="revenue",
        fiscal_period="Q2",
        authoritative_only=True,
        engine=db_engine,
    )
    latest_q2 = get_latest_authoritative_observation(
        "OBS1",
        metric_name="revenue",
        fiscal_period="Q2",
        engine=db_engine,
    )

    assert len(all_q2_rows) == 2
    assert len(authoritative_q2_rows) == 1
    assert latest_q2 is not None
    assert latest_q2.value == Decimal("260.000000")
    assert latest_q2.accession_number == "0002A"


def test_build_fundamental_history_groups_annual_and_quarterly_points(db_engine):
    security = Security(ticker="HIST1", name="History One", exchange="NYSE", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    upsert_fundamental_observations(
        [
            FundamentalObservation(
                security_id=security.id,
                metric_name="revenue",
                value=Decimal("130.000000"),
                unit="USD",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 3, 31),
                fiscal_year=2025,
                fiscal_period="Q1",
                form_type="10-Q",
                filed_at=date(2025, 4, 20),
                accession_number="1001",
                source_concept="Revenues",
                source_url="sec://q1",
                is_derived=False,
            ),
            FundamentalObservation(
                security_id=security.id,
                metric_name="revenue",
                value=Decimal("690.000000"),
                unit="USD",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                fiscal_year=2025,
                fiscal_period="FY",
                form_type="10-K",
                filed_at=date(2026, 2, 10),
                accession_number="1004",
                source_concept="Revenues",
                source_url="sec://fy",
                is_derived=False,
            ),
        ],
        engine=db_engine,
    )

    history = build_fundamental_history("hist1", engine=db_engine)

    assert history.ticker == "HIST1"
    assert len(history.series) == 1
    assert history.series[0].metric_name == "revenue"
    assert [point.fiscal_period for point in history.series[0].annual] == ["FY"]
    assert [point.fiscal_period for point in history.series[0].quarterly] == ["Q1"]
    assert history.series[0].annual[0].value == Decimal("690.000000")


def test_list_active_securities_filters_inactive_rows(db_engine):
    with Session(db_engine) as session:
        session.add(Security(ticker="ACT1", name="Active One", exchange="NASDAQ", sector_tag="ai", is_active=True))
        session.add(Security(ticker="INACT1", name="Inactive One", exchange="NYSE", sector_tag="ai", is_active=False))
        session.commit()

    active = list_active_securities(engine=db_engine)
    active_tickers = [security.ticker for security in active]
    assert "ACT1" in active_tickers
    assert "INACT1" not in active_tickers


def test_get_latest_macro_daily_returns_latest_vix(db_engine):
    rows = [
        MacroDaily(obs_date=date(2026, 1, 1), vix=Decimal("18.0")),
        MacroDaily(obs_date=date(2026, 1, 2), vix=Decimal("22.5")),
        MacroDaily(obs_date=date(2026, 1, 3), vix=Decimal("19.0")),
    ]
    upsert_macro_daily_rows(rows, engine=db_engine)

    latest = get_latest_macro_daily(engine=db_engine)
    assert latest is not None
    assert latest.obs_date == date(2026, 1, 3)
    assert latest.vix == Decimal("19.0")


def test_upsert_technical_feature_idempotent(db_engine):
    security = Security(ticker="TECH2", name="Tech Two", exchange="NASDAQ", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    feature = TechnicalFeature(
        security_id=security.id,
        as_of_date=date(2026, 1, 1),
        rsi_14=Decimal("55.0000"),
        sma_50=Decimal("100.5000"),
        sma_200=Decimal("95.2500"),
        volume_trend=Decimal("1.0500"),
    )

    upsert_technical_feature(feature, engine=db_engine)
    upsert_technical_feature(feature, engine=db_engine)

    with Session(db_engine) as session:
        rows = session.exec(select(TechnicalFeature).where(TechnicalFeature.security_id == security.id)).all()
        assert len(rows) == 1
        assert rows[0].rsi_14 == Decimal("55.0000")


def test_upsert_technical_feature_updates_existing(db_engine):
    security = Security(ticker="TECH3", name="Tech Three", exchange="NASDAQ", sector_tag="ai")
    with Session(db_engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)

    initial = TechnicalFeature(
        security_id=security.id,
        as_of_date=date(2026, 1, 1),
        rsi_14=Decimal("45.0000"),
        sma_50=None,
        sma_200=None,
        volume_trend=None,
    )
    upsert_technical_feature(initial, engine=db_engine)

    updated = TechnicalFeature(
        security_id=security.id,
        as_of_date=date(2026, 1, 1),
        rsi_14=Decimal("55.0000"),
        sma_50=Decimal("100.0000"),
        sma_200=Decimal("95.0000"),
        volume_trend=Decimal("1.2000"),
    )
    upsert_technical_feature(updated, engine=db_engine)

    with Session(db_engine) as session:
        rows = session.exec(select(TechnicalFeature).where(TechnicalFeature.security_id == security.id)).all()
        assert len(rows) == 1
        assert rows[0].rsi_14 == Decimal("55.0000")
        assert rows[0].sma_50 == Decimal("100.0000")
        assert rows[0].volume_trend == Decimal("1.2000")
