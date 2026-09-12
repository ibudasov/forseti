from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, List, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import select

from sqlalchemy import func

from app.db.models import (
    AgentRun,
    AgentRunStep,
    DocumentChunk,
    EarningsEvent,
    Fundamental,
    FundamentalObservation,
    MacroDaily,
    PriceBar,
    Recommendation,
    Security,
    SourceType,
    TechnicalFeature,
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


def upsert_fundamental_observations(
    observations: Iterable[FundamentalObservation],
    engine=None,
) -> None:
    engine = engine or get_engine()
    payloads = [observation.model_dump(exclude_none=False) for observation in observations]
    payloads = [payload for payload in payloads if payload]
    if not payloads:
        return

    for payload in payloads:
        payload.pop("id", None)

    stmt = pg_insert(FundamentalObservation.__table__).values(payloads)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_fundamental_observation_ingest",
        set_={
            "value": stmt.excluded.value,
            "filed_at": stmt.excluded.filed_at,
            "source_url": stmt.excluded.source_url,
            "derivation": stmt.excluded.derivation,
            "ingested_at": stmt.excluded.ingested_at,
        },
    )

    with get_session(engine) as session:
        session.execute(stmt)
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


def save_agent_run(run: AgentRun, steps: Iterable[AgentRunStep], engine=None) -> AgentRun:
    engine = engine or get_engine()
    with get_session(engine) as session:
        session.add(run)
        session.add_all(list(steps))
        session.commit()
        session.refresh(run)
        return run


def get_agent_run(run_id: str, engine=None) -> Optional[AgentRun]:
    engine = engine or get_engine()
    with get_session(engine) as session:
        return session.get(AgentRun, run_id)


def get_agent_run_steps(run_id: str, engine=None) -> List[AgentRunStep]:
    engine = engine or get_engine()
    statement = select(AgentRunStep).where(AgentRunStep.run_id == run_id).order_by(AgentRunStep.sequence)
    with get_session(engine) as session:
        return list(session.exec(statement).all())


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


def count_earnings_events(ticker: str, engine=None) -> int:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    statement = (
        select(func.count())
        .select_from(EarningsEvent)
        .join(Security)
        .where(Security.ticker == ticker)
    )
    with get_session(engine) as session:
        return session.exec(statement).one()


def count_document_chunks(ticker: str, engine=None) -> int:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    statement = select(func.count()).select_from(DocumentChunk).where(DocumentChunk.ticker == ticker)
    with get_session(engine) as session:
        return session.exec(statement).one()


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


def list_fundamentals(engine=None, ticker: str | None = None) -> List[Fundamental]:
    engine = engine or get_engine()
    stmt = select(Fundamental)
    if ticker is not None:
        stmt = stmt.join(Security).where(Security.ticker == _normalize_ticker(ticker))
    stmt = stmt.order_by(Fundamental.security_id.asc(), Fundamental.as_of_date.desc())
    with get_session(engine) as session:
        return list(session.exec(stmt).all())


def list_fundamental_observations(
    ticker: str,
    *,
    metric_name: str | None = None,
    fiscal_period: str | None = None,
    authoritative_only: bool = False,
    engine=None,
) -> List[FundamentalObservation]:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    stmt = (
        select(FundamentalObservation)
        .join(Security)
        .where(Security.ticker == ticker)
    )
    if metric_name is not None:
        stmt = stmt.where(FundamentalObservation.metric_name == metric_name)
    if fiscal_period is not None:
        stmt = stmt.where(FundamentalObservation.fiscal_period == fiscal_period)
    stmt = stmt.order_by(
        FundamentalObservation.metric_name.asc(),
        FundamentalObservation.fiscal_period.asc(),
        FundamentalObservation.period_end.asc(),
        sa.nullslast(FundamentalObservation.filed_at.desc()),
        sa.nullslast(FundamentalObservation.accession_number.desc()),
        FundamentalObservation.id.desc(),
    )

    with get_session(engine) as session:
        observations = list(session.exec(stmt).all())

    if not authoritative_only:
        return observations
    return _authoritative_observations(observations)


def get_latest_authoritative_observation(
    ticker: str,
    metric_name: str,
    fiscal_period: str,
    engine=None,
) -> Optional[FundamentalObservation]:
    observations = list_fundamental_observations(
        ticker,
        metric_name=metric_name,
        fiscal_period=fiscal_period,
        authoritative_only=True,
        engine=engine,
    )
    return observations[-1] if observations else None


def count_fundamental_observations(ticker: str, engine=None) -> int:
    engine = engine or get_engine()
    ticker = _normalize_ticker(ticker)
    stmt = (
        select(func.count())
        .select_from(FundamentalObservation)
        .join(Security)
        .where(Security.ticker == ticker)
    )
    with get_session(engine) as session:
        return session.exec(stmt).one()


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
    upsert_technical_features([feature], engine=engine)


def upsert_technical_features(features: Iterable[TechnicalFeature], engine=None) -> None:
    engine = engine or get_engine()
    payloads = [feature.model_dump(exclude_none=True) for feature in features]
    if not payloads:
        return

    stmt = pg_insert(TechnicalFeature.__table__).values(payloads)
    stmt = stmt.on_conflict_do_update(
        index_elements=[TechnicalFeature.security_id, TechnicalFeature.as_of_date],
        set_={
            "rsi_14": stmt.excluded.rsi_14,
            "sma_50": stmt.excluded.sma_50,
            "sma_200": stmt.excluded.sma_200,
            "volume_trend": stmt.excluded.volume_trend,
        },
    )

    with get_session(engine) as session:
        session.execute(stmt)
        session.commit()


# ---------------------------------------------------------------------------
# DocumentChunk repository
# ---------------------------------------------------------------------------

def upsert_document_chunks(chunks: Iterable[DocumentChunk], engine=None) -> None:
    """Insert document chunks, skipping duplicates by source_hash (idempotent)."""
    engine = engine or get_engine()
    payloads = [chunk.model_dump(exclude_none=False) for chunk in chunks]
    payloads = [p for p in payloads if p]
    if not payloads:
        return

    # Exclude id and ingested_at so the DB default applies
    for payload in payloads:
        payload.pop("id", None)

    stmt = pg_insert(DocumentChunk.__table__).values(payloads)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_document_chunk_source_hash")

    with get_session(engine) as session:
        session.execute(stmt)
        session.commit()


def similarity_search(
    ticker: str,
    query_embedding: List[float],
    top_k: int = 5,
    source_types: Optional[List[SourceType]] = None,
    published_after: Optional[datetime] = None,
    engine=None,
) -> List[DocumentChunk]:
    """Return the *top_k* most similar chunks for *ticker*.

    Similarity is measured by cosine distance via pgvector operator ``<=>``.
    Ordering: score ASC (nearest), then published_at DESC, then id ASC.
    """
    engine = engine or get_engine()
    table = DocumentChunk.__table__

    embedding_col = sa.cast(
        sa.literal(query_embedding, type_=sa.ARRAY(sa.Float)),
        DocumentChunk.__table__.c.embedding.type,
    )
    distance = table.c.embedding.op("<=>")(embedding_col)

    stmt = sa.select(table).where(table.c.ticker == ticker.strip().upper())

    if source_types:
        stmt = stmt.where(table.c.source_type.in_([st.value for st in source_types]))

    if published_after is not None:
        stmt = stmt.where(
            sa.or_(table.c.published_at.is_(None), table.c.published_at >= published_after)
        )

    stmt = stmt.order_by(
        distance.asc(),
        sa.nullslast(table.c.published_at.desc()),
        table.c.id.asc(),
    ).limit(top_k)

    with get_session(engine) as session:
        rows = session.execute(stmt).fetchall()
        return [DocumentChunk(**dict(row._mapping)) for row in rows]


def _authoritative_observations(
    observations: List[FundamentalObservation],
) -> List[FundamentalObservation]:
    winners: dict[tuple[str, str, date], FundamentalObservation] = {}
    for observation in observations:
        key = (
            observation.metric_name,
            observation.fiscal_period,
            observation.period_end,
        )
        current = winners.get(key)
        if current is None or _observation_sort_key(observation) > _observation_sort_key(current):
            winners[key] = observation
    return sorted(
        winners.values(),
        key=lambda observation: (
            observation.metric_name,
            observation.fiscal_period,
            observation.period_end,
        ),
    )


def _observation_sort_key(observation: FundamentalObservation) -> tuple[date, str, str, int]:
    return (
        observation.filed_at or date.min,
        observation.accession_number or "",
        observation.source_url,
        observation.id or 0,
    )
