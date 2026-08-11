from __future__ import annotations

from typing import Optional

from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import Engine, create_mock_engine
from sqlalchemy.pool import Pool
from sqlmodel import Session

from app.settings import get_settings


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
    connect_args = {}
    if url.startswith("postgresql"):
        connect_args = {"options": "-c timezone=utc"}

    engine = create_engine(url, echo=False, future=True, connect_args=connect_args)
    
    # Register event listener to refresh collation on each new connection
    # This ensures collation version is always in sync with the OS
    if url.startswith("postgresql"):
        event.listen(engine.pool, "connect", _refresh_collation_version)
    
    return engine


def get_session(engine: Optional[Engine] = None) -> Session:
    return Session(engine or get_engine(), expire_on_commit=False)


def create_db_and_tables(engine: Optional[Engine] = None) -> None:
    from app.db.models import SQLModel

    engine = engine or get_engine()
    SQLModel.metadata.create_all(engine)
