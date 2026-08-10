from __future__ import annotations

from datetime import date
from typing import Iterable, List, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from sqlalchemy import func

from app.db.models import (
    EarningsEvent,
    Fundamental,
    MacroDaily,
    PriceBar,
    Recommendation,
    Security,
    TechnicalFeature,
    DocumentChunk,
    DocumentSourceType,
)
from app.db.session import get_engine, get_session


def _normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def upsert_price_bars(bars: Iterable[PriceBar], engine=None) -> None:
    engine = engine or get_engine()
    payloads = [bar.model_dump(exclude_none=True) for bar in bars]
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


def upsert_fundamental(fundamental: Fundamental, engine=None) -> None:
    engine = engine or get_engine()
    stmt = (
        select(Fundamental)
        .where(Fundamental.security_id == fundamental.security_id)
        .where(Fundamental.as_of_date == fundamental.as_of_date)
    )

    with get_session(engine) as session:
        existing = session.exec(stmt).first()
        if existing is None:
            session.add(fundamental)
            session.commit()
            return

        existing.revenue_growth = fundamental.revenue_growth
        existing.fcf = fundamental.fcf
        existing.debt_to_equity = fundamental.debt_to_equity
        existing.eps_trend = fundamental.eps_trend
        existing.margins = fundamental.margins
        existing.raw_payload = fundamental.raw_payload
        session.add(existing)
        session.commit()


def upsert_earnings_event(event: EarningsEvent, engine=None) -> None:
    upsert_earnings_events([event], engine=engine)


def upsert_earnings_events(events: Iterable[EarningsEvent], engine=None) -> None:
    engine = engine or get_engine()
    payloads = [event.model_dump(exclude_none=True) for event in events]
    if not payloads:
        return

    stmt = pg_insert(EarningsEvent.__table__).values(payloads)
    stmt = stmt.on_conflict_do_update(
        index_elements=[EarningsEvent.security_id, EarningsEvent.report_date],
        set_={"confirmed": stmt.excluded.confirmed},
    )

    with get_session(engine) as session:
        session.execute(stmt)
        session.commit()


def upsert_macro_daily(row: MacroDaily, engine=None) -> None:
    upsert_macro_daily_rows([row], engine=engine)


def upsert_macro_daily_rows(rows: Iterable[MacroDaily], engine=None) -> None:
    engine = engine or get_engine()
    payloads = [row.model_dump(exclude_none=True) for row in rows]
    if not payloads:
        return

    stmt = pg_insert(MacroDaily.__table__).values(payloads)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MacroDaily.obs_date],
        set_={"vix": stmt.excluded.vix},
    )

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


def list_active_securities(engine=None) -> List[Security]:
    engine = engine or get_engine()
    stmt = select(Security).where(Security.is_active.is_(True)).order_by(Security.ticker.asc())

    with get_session(engine) as session:
        return session.exec(stmt).all()


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


def count_price_bars(ticker: str, engine=None) -> int:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    stmt = (
        select(func.count())
        .select_from(PriceBar)
        .join(Security)
        .where(Security.ticker == ticker)
    )
    with get_session(engine) as session:
        return session.exec(stmt).one()


def get_latest_technical_feature(ticker: str, engine=None) -> Optional[TechnicalFeature]:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    stmt = (
        select(TechnicalFeature)
        .join(Security)
        .where(Security.ticker == ticker)
        .order_by(TechnicalFeature.as_of_date.desc())
        .limit(1)
    )
    with get_session(engine) as session:
        return session.exec(stmt).first()


def get_latest_fundamental(ticker: str, engine=None) -> Optional[Fundamental]:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    stmt = (
        select(Fundamental)
        .join(Security)
        .where(Security.ticker == ticker)
        .order_by(Fundamental.as_of_date.desc())
        .limit(1)
    )
    with get_session(engine) as session:
        return session.exec(stmt).first()


def get_next_earnings_event(ticker: str, on_or_after: date, engine=None) -> Optional[EarningsEvent]:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    stmt = (
        select(EarningsEvent)
        .join(Security)
        .where(Security.ticker == ticker)
        .where(EarningsEvent.report_date >= on_or_after)
        .order_by(EarningsEvent.report_date.asc())
        .limit(1)
    )
    with get_session(engine) as session:
        return session.exec(stmt).first()


def get_latest_macro_daily(engine=None) -> Optional[MacroDaily]:
    engine = engine or get_engine()
    stmt = select(MacroDaily).order_by(MacroDaily.obs_date.desc()).limit(1)
    with get_session(engine) as session:
        return session.exec(stmt).first()


def upsert_technical_feature(feature: TechnicalFeature, engine=None) -> None:
    engine = engine or get_engine()
    security_id = feature.security_id
    as_of_date = feature.as_of_date
    rsi_14 = feature.rsi_14
    sma_50 = feature.sma_50
    sma_200 = feature.sma_200
    volume_trend = feature.volume_trend
    stmt = (
        select(TechnicalFeature)
        .where(TechnicalFeature.security_id == security_id)
        .where(TechnicalFeature.as_of_date == as_of_date)
    )

    with get_session(engine) as session:
        existing = session.exec(stmt).first()
        if existing is None:
            session.add(
                TechnicalFeature(
                    security_id=security_id,
                    as_of_date=as_of_date,
                    rsi_14=rsi_14,
                    sma_50=sma_50,
                    sma_200=sma_200,
                    volume_trend=volume_trend,
                )
            )
            session.commit()
            return

        existing.rsi_14 = rsi_14
        existing.sma_50 = sma_50
        existing.sma_200 = sma_200
        existing.volume_trend = volume_trend
        session.add(existing)
        session.commit()


def create_document_chunk(chunk: DocumentChunk, engine=None) -> DocumentChunk:
    """Create a single document chunk. Raises if source_hash already exists."""
    engine = engine or get_engine()
    with get_session(engine) as session:
        session.add(chunk)
        session.commit()
        session.refresh(chunk)
        return chunk


def bulk_create_document_chunks(chunks: Iterable[DocumentChunk], engine=None) -> int:
    """Bulk create document chunks with idempotency via source_hash.
    
    Returns the number of chunks created (skips duplicates).
    """
    engine = engine or get_engine()
    payloads = [chunk.model_dump(exclude_none=True) for chunk in chunks]
    if not payloads:
        return 0

    stmt = pg_insert(DocumentChunk.__table__).values(payloads)
    stmt = stmt.on_conflict_do_nothing(index_elements=[DocumentChunk.source_hash])

    with get_session(engine) as session:
        result = session.execute(stmt)
        session.commit()
        return result.rowcount or 0


def get_document_chunks_by_ticker(
    ticker: str,
    source_type: Optional[DocumentSourceType] = None,
    limit: int = 100,
    engine=None,
) -> List[DocumentChunk]:
    """Get document chunks for a ticker, optionally filtered by source type."""
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    stmt = select(DocumentChunk).where(DocumentChunk.ticker == ticker)
    
    if source_type:
        stmt = stmt.where(DocumentChunk.source_type == source_type)
    
    stmt = stmt.order_by(DocumentChunk.ingested_at.desc()).limit(limit)

    with get_session(engine) as session:
        return session.exec(stmt).all()


def delete_document_chunks_by_source_hash(source_hash: str, engine=None) -> bool:
    """Delete a document chunk by source_hash. Returns True if deleted."""
    engine = engine or get_engine()
    stmt = select(DocumentChunk).where(DocumentChunk.source_hash == source_hash)

    with get_session(engine) as session:
        chunk = session.exec(stmt).first()
        if chunk is None:
            return False
        session.delete(chunk)
        session.commit()
        return True
