from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlmodel import Session

from app.db.models import EarningsEvent, MacroDaily, PriceBar, Security, TechnicalFeature
from app.db.repository import upsert_price_bars, upsert_technical_feature
from app.services.technical_context import (
    HISTORY_BARS_LOADED,
    SERIES_WINDOW,
    build_technical_analysis_request,
)


def test_build_request_is_deterministic_for_same_snapshot(db_engine):
    _seed_security_with_bars(db_engine, ticker="CTX2", bar_count=70)
    snapshot_at = datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc)

    first = build_technical_analysis_request(
        "ctx2",
        "run-a",
        as_of=date(2026, 9, 8),
        engine=db_engine,
        snapshot_at=snapshot_at,
    )
    second = build_technical_analysis_request(
        "CTX2",
        "run-b",
        as_of=date(2026, 9, 8),
        engine=db_engine,
        snapshot_at=snapshot_at,
    )

    assert first.context_hash == second.context_hash
    assert first.model_dump(exclude={"run_id"}, mode="json") == second.model_dump(
        exclude={"run_id"},
        mode="json",
    )


def test_build_request_ignores_future_bars_no_look_ahead(db_engine):
    ticker = "CTX3"
    security = _seed_security_with_bars(db_engine, ticker=ticker, bar_count=70)
    as_of_date = date(2026, 9, 8)

    # Insert a bar dated after as_of_date - it must never be read.
    future_bar = PriceBar(
        security_id=security.id,
        bar_date=as_of_date + timedelta(days=1),
        open=Decimal("999"),
        high=Decimal("999"),
        low=Decimal("999"),
        close=Decimal("999"),
        volume=1,
    )
    upsert_price_bars([future_bar], engine=db_engine)

    request = build_technical_analysis_request(
        ticker,
        "run-a",
        as_of=as_of_date,
        engine=db_engine,
    )

    assert request.as_of_date == as_of_date
    assert request.coverage.latest_bar_date == as_of_date
    assert all(point.bar_date <= as_of_date for point in request.series)
    close_fact = next(fact for fact in request.facts if fact.name == "close")
    assert close_fact.value != Decimal("999")


def test_build_request_respects_schema_invariants(db_engine):
    _seed_security_with_bars(db_engine, ticker="CTX4", bar_count=270)
    request = build_technical_analysis_request(
        "CTX4",
        "run-a",
        as_of=date(2026, 9, 8),
        engine=db_engine,
    )

    assert request.allowed_adjustment_min == -2
    assert request.allowed_adjustment_max == 0
    assert len(request.series) == SERIES_WINDOW
    assert request.coverage.bar_count <= HISTORY_BARS_LOADED
    assert request.coverage.series_truncated is True
    metric_ids = [fact.metric_id for fact in request.facts]
    assert len(metric_ids) == len(set(metric_ids))

    # Every metric cited by the TA-1 baseline must be present under the same metric_id.
    baseline_metric_ids = {metric.metric_id for metric in request.deterministic_result.metrics}
    assert baseline_metric_ids <= set(metric_ids)


def test_build_request_payload_size_is_bounded(db_engine):
    _seed_security_with_bars(db_engine, ticker="CTX5", bar_count=260)
    request = build_technical_analysis_request(
        "CTX5",
        "run-a",
        as_of=date(2026, 9, 8),
        engine=db_engine,
    )
    payload = request.model_dump_json()
    assert len(payload.encode("utf-8")) < 15_000


def test_build_request_handles_missing_technical_feature_and_vix(db_engine):
    with Session(db_engine) as session:
        security = Security(ticker="CTX6", name="Context Six", exchange="NASDAQ", sector_tag="ai")
        session.add(security)
        session.commit()
        session.refresh(security)

    bars = _make_bars(security.id, date(2026, 6, 1), 20)
    upsert_price_bars(bars, engine=db_engine)

    request = build_technical_analysis_request(
        "CTX6",
        "run-a",
        as_of=date(2026, 6, 20),
        engine=db_engine,
    )

    assert request.coverage.bar_count == 20
    assert "no_technical_features" in request.deterministic_result.warnings
    assert "no_vix" in request.deterministic_result.warnings
    # TA-1 omits sma/rsi/volume_trend metrics entirely when TechnicalFeature is
    # missing (it does not emit them with a None value), so no "volume_trend"
    # fact is cited either.
    assert not any(fact.name == "volume_trend" for fact in request.facts)
    assert any(fact.name == "close" for fact in request.facts)
    assert "dist_sma50_pct" in request.coverage.required_facts_missing


def test_build_request_cites_next_earnings_date(db_engine):
    ticker = "CTX7"
    security = _seed_security_with_bars(db_engine, ticker=ticker, bar_count=70)
    with Session(db_engine) as session:
        session.add(EarningsEvent(security_id=security.id, report_date=date(2026, 9, 15), confirmed=True))
        session.commit()

    request = build_technical_analysis_request(
        ticker,
        "run-a",
        as_of=date(2026, 9, 8),
        engine=db_engine,
    )
    days_to_earnings_fact = next(fact for fact in request.facts if fact.name == "days_to_earnings")
    assert days_to_earnings_fact.value == Decimal("7")


def _seed_security_with_bars(db_engine, *, ticker: str, bar_count: int) -> Security:
    with Session(db_engine) as session:
        security = Security(ticker=ticker, name=f"{ticker} Inc.", exchange="NASDAQ", sector_tag="ai")
        session.add(security)
        session.commit()
        session.refresh(security)

    start_date = date(2026, 9, 8) - timedelta(days=bar_count - 1)
    bars = _make_bars(security.id, start_date, bar_count)
    upsert_price_bars(bars, engine=db_engine)
    upsert_technical_feature(
        TechnicalFeature(
            security_id=security.id,
            as_of_date=date(2026, 9, 8),
            rsi_14=Decimal("55.0000"),
            sma_50=Decimal("95.0000"),
            sma_200=Decimal("90.0000"),
            volume_trend=Decimal("1.1000"),
        ),
        engine=db_engine,
    )
    with Session(db_engine) as session:
        session.add(MacroDaily(obs_date=date(2026, 9, 8), vix=Decimal("18.500")))
        session.commit()
    return security


def _make_bars(security_id: int, start_date: date, count: int) -> list[PriceBar]:
    bars = []
    for i in range(count):
        close = Decimal(100 + i)
        bars.append(
            PriceBar(
                security_id=security_id,
                bar_date=start_date + timedelta(days=i),
                open=close - 1,
                high=close + 1,
                low=close - 2,
                close=close,
                volume=1_000 + i,
            )
        )
    return bars
