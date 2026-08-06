from __future__ import annotations

from datetime import date
from typing import Iterable, List, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from app.db.models import (
    EarningsEvent,
    MacroDaily,
    PriceBar,
    Recommendation,
    Security,
)
from app.db.session import get_engine, get_session


def _normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def upsert_price_bars(bars: Iterable[PriceBar], engine=None) -> None:
    engine = engine or get_engine()
    payloads = [bar.dict(exclude_none=True) for bar in bars]
    if not payloads:
        return

    stmt = pg_insert(PriceBar.__table__).values(payloads)
    stmt = stmt.on_conflict_do_update(
        index_elements=[PriceBar.security_id, PriceBar.bar_date],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
        },
    )

    with get_session(engine) as session:
        session.execute(stmt)
        session.commit()


def upsert_earnings_event(event: EarningsEvent, engine=None) -> None:
    engine = engine or get_engine()
    stmt = pg_insert(EarningsEvent.__table__).values(event.dict(exclude_none=True))
    stmt = stmt.on_conflict_do_nothing(index_elements=[EarningsEvent.security_id, EarningsEvent.report_date])

    with get_session(engine) as session:
        session.execute(stmt)
        session.commit()


def upsert_macro_daily(row: MacroDaily, engine=None) -> None:
    engine = engine or get_engine()
    stmt = pg_insert(MacroDaily.__table__).values(row.dict(exclude_none=True))
    stmt = stmt.on_conflict_do_nothing(index_elements=[MacroDaily.obs_date])

    with get_session(engine) as session:
        session.execute(stmt)
        session.commit()


def save_recommendation(rec: Recommendation, engine=None) -> Recommendation:
    if rec.id is not None:
        raise ValueError("Recommendation save refused: append-only records must not contain an existing id.")

    engine = engine or get_engine()
    with get_session(engine) as session:
        session.add(rec)
        session.commit()
        session.refresh(rec)
        return rec


def get_security(ticker: str, engine=None) -> Optional[Security]:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    stmt = select(Security).where(Security.ticker == ticker)

    with get_session(engine) as session:
        return session.exec(stmt).first()


def get_latest_bars(ticker: str, n: int, engine=None, as_of_date: Optional[date] = None) -> List[PriceBar]:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    stmt = (
        select(PriceBar)
        .join(Security)
        .where(Security.ticker == ticker)
    )
    if as_of_date is not None:
        stmt = stmt.where(PriceBar.bar_date <= as_of_date)

    stmt = stmt.order_by(PriceBar.bar_date.desc()).limit(n)

    with get_session(engine) as session:
        return session.exec(stmt).all()
