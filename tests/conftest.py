"""Shared database fixtures.

Every fixture here drops and recreates all tables, so it refuses to touch a
database whose name is not explicitly marked as a test database.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine as create_sqlalchemy_engine, text
from sqlmodel import SQLModel, create_engine
from testcontainers.community.postgres import PostgresContainer

TEST_DATABASE_SUFFIX = "_test"
DISPOSABLE_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_]+" + TEST_DATABASE_SUFFIX + r"$")
PGVECTOR_IMAGE = "pgvector/pgvector:pg15"


def _database_name(database_url: str) -> str:
    return urlparse(database_url).path.lstrip("/")


def _assert_database_is_disposable(database_url: str) -> None:
    database_name = _database_name(database_url)
    if not DISPOSABLE_DATABASE_NAME.match(database_name):
        raise RuntimeError(
            f"Refusing to drop tables in database '{database_name}': "
            f"TEST_DATABASE_URL must point at a database named '<name>{TEST_DATABASE_SUFFIX}'."
        )


def _create_database_if_missing(database_url: str) -> None:
    database_name = _database_name(database_url)
    maintenance_url = database_url.rsplit("/", 1)[0] + "/postgres"
    maintenance_engine = create_sqlalchemy_engine(maintenance_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with maintenance_engine.connect() as connection:
            already_exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database_name}
            ).scalar()
            if not already_exists:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        maintenance_engine.dispose()


def _prepare_engine(database_url: str):
    engine = create_engine(database_url, echo=False, future=True)
    with engine.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.commit()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_engine():
    configured_url = os.getenv("TEST_DATABASE_URL")
    if configured_url:
        _assert_database_is_disposable(configured_url)
        _create_database_if_missing(configured_url)
        engine = _prepare_engine(configured_url)
        yield engine
        engine.dispose()
        return

    with PostgresContainer(PGVECTOR_IMAGE) as postgres:
        container_url = postgres.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        engine = _prepare_engine(container_url)
        yield engine
        engine.dispose()


@pytest.fixture
def pgvector_engine(db_engine):
    return db_engine
