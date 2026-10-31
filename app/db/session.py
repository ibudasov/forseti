from __future__ import annotations

from typing import Optional
from threading import RLock

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL, make_url
from sqlmodel import Session

from app.settings import get_settings

_engines: dict[str, Engine] = {}
_engine_lock = RLock()


def get_engine(database_url: Optional[str] = None) -> Engine:
    settings = get_settings()
    url = database_url or settings.DATABASE_URL
    with _engine_lock:
        if url not in _engines:
            _engines[url] = _create_engine(url)
        return _engines[url]


def normalize_database_url(database_url: str) -> URL:
    engine_url = make_url(database_url)
    if engine_url.drivername != "postgresql":
        return engine_url
    return engine_url.set(drivername="postgresql+psycopg2")


def _create_engine(url: str) -> Engine:
    settings = get_settings()
    connect_args = {}
    pool_options = {}
    engine_url = normalize_database_url(url)

    if url.startswith("postgresql"):
        connect_args = {"options": "-c timezone=utc"}
        pool_options = {"pool_size": settings.DB_POOL_SIZE, "max_overflow": settings.DB_MAX_OVERFLOW}

    return create_engine(
        engine_url, echo=False, future=True, connect_args=connect_args,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS, pool_pre_ping=True, **pool_options,
    )


def dispose_engines() -> None:
    """Dispose all process-local pools and reset the cache (also used by tests)."""
    with _engine_lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


def get_session(engine: Optional[Engine] = None) -> Session:
    return Session(engine or get_engine(), expire_on_commit=False)


def create_db_and_tables(engine: Optional[Engine] = None) -> None:
    from app.db.models import SQLModel

    engine = engine or get_engine()
    SQLModel.metadata.create_all(engine)
