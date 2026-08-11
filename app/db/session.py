from __future__ import annotations

from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.settings import get_settings


def get_engine(database_url: Optional[str] = None) -> Engine:
    settings = get_settings()
    url = database_url or settings.DATABASE_URL
    connect_args = {}
    if url.startswith("postgresql"):
        connect_args = {"options": "-c timezone=utc"}

    return create_engine(url, echo=False, future=True, connect_args=connect_args)


def get_session(engine: Optional[Engine] = None) -> Session:
    return Session(engine or get_engine(), expire_on_commit=False)


def create_db_and_tables(engine: Optional[Engine] = None) -> None:
    from app.db.models import SQLModel

    engine = engine or get_engine()
    SQLModel.metadata.create_all(engine)
