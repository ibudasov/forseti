from __future__ import annotations

from typing import Optional
from threading import RLock

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.settings import get_settings

_engines: dict[str, Engine] = {}
_engine_lock = RLock()


def _refresh_collation_version(dbapi_connection, connection_record):
    """Refresh database collation version on new connection.

    This resolves PostgreSQL collation version mismatches that occur when
    the database was created with a different collation library version
    than the current OS provides.
    """
    if not dbapi_connection.closed:
        try:
            # Only attempt for PostgreSQL connections
            if "psycopg" in dbapi_connection.__class__.__module__:
                cursor = dbapi_connection.cursor()
                # Get database name from the connection
                cursor.execute("SELECT current_database();")
                db_name = cursor.fetchone()[0]
                cursor.close()
                # Refresh collation version (requires superuser or database owner)
                cursor = dbapi_connection.cursor()
                cursor.execute(f"ALTER DATABASE {db_name} REFRESH COLLATION VERSION;")
                cursor.close()
                dbapi_connection.commit()
        except Exception as e:
            # Log but don't fail - this is a non-critical maintenance operation
            print(f"Note: Could not refresh collation version: {e}")


def get_engine(database_url: Optional[str] = None) -> Engine:
    settings = get_settings()
    url = database_url or settings.DATABASE_URL
    with _engine_lock:
        if url not in _engines:
            _engines[url] = _create_engine(url)
        return _engines[url]


def _create_engine(url: str) -> Engine:
    settings = get_settings()
    connect_args = {}
    pool_options = {}
    if url.startswith("postgresql"):
        connect_args = {"options": "-c timezone=utc"}
        pool_options = {"pool_size": settings.DB_POOL_SIZE, "max_overflow": settings.DB_MAX_OVERFLOW}

    engine = create_engine(
        url, echo=False, future=True, connect_args=connect_args,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS, pool_pre_ping=True, **pool_options,
    )
    # Register event listener to refresh collation on each new connection
    # This ensures collation version is always in sync with the OS
    if url.startswith("postgresql"):
        event.listen(engine.pool, "connect", _refresh_collation_version)
    return engine


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
