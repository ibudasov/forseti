"""Privileged database housekeeping: extensions, migrations, runtime roles and collation.

This is the only code that changes the schema, manages roles or alters the
database. It runs as the owner role (the private ``forseti-housekeeping`` Cloud
Run Job in the cloud, the compose superuser locally). Every step is idempotent.

It deliberately does not use ``app.runtime.run_job``: that validates the serving
configuration (``DATABASE_URL``, Vertex settings), which this job must not need.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg2 import sql
from sqlalchemy import create_engine, pool

from app.db.session import normalize_database_url
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TABLE_PRIVILEGES = sql.SQL("SELECT, INSERT, UPDATE, DELETE")
SEQUENCE_PRIVILEGES = sql.SQL("USAGE, SELECT")
STEP_NAMES = ("extensions", "migrate", "roles", "collation")


@dataclass(frozen=True)
class HousekeepingContext:
    database_url: str
    app_role: str
    app_password: Optional[str]
    password_required: bool
    dry_run: bool = False
    reindex: bool = False


@dataclass(frozen=True)
class StepResult:
    step: str
    changed: bool
    detail: str


@contextmanager
def _autocommit_cursor(database_url: str) -> Iterator:
    engine = create_engine(
        normalize_database_url(database_url), poolclass=pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    connection = engine.raw_connection()
    try:
        yield connection.cursor()
    finally:
        connection.close()
        engine.dispose()


def _fetch_one(cursor, query, parameters=None):
    cursor.execute(query, parameters)
    return cursor.fetchone()


def ensure_extensions(context: HousekeepingContext) -> StepResult:
    with _autocommit_cursor(context.database_url) as cursor:
        installed = _fetch_one(cursor, "SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        if installed:
            return StepResult("extensions", False, "vector already installed")
        if context.dry_run:
            return StepResult("extensions", True, "would install vector")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        return StepResult("extensions", True, "installed vector")


def _alembic_config(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.attributes["database_url"] = database_url
    return config


def _current_revision(database_url: str) -> Optional[str]:
    engine = create_engine(normalize_database_url(database_url), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def migrate_schema(context: HousekeepingContext) -> StepResult:
    config = _alembic_config(context.database_url)
    head = ScriptDirectory.from_config(config).get_current_head()
    before = _current_revision(context.database_url)
    if before == head:
        return StepResult("migrate", False, f"already at {head}")
    if context.dry_run:
        return StepResult("migrate", True, f"would upgrade {before} -> {head}")
    command.upgrade(config, "head")
    return StepResult("migrate", True, f"upgraded {before} -> {head}")


def _role_exists(cursor, role: str) -> bool:
    return _fetch_one(cursor, "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)) is not None


def _upsert_login_role(cursor, role: str, password: str, exists: bool) -> None:
    verb = "ALTER" if exists else "CREATE"
    cursor.execute(
        sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            sql.SQL(verb), sql.Identifier(role), sql.Literal(password)
        )
    )


def _grant_data_access(cursor, role: str) -> None:
    app_role = sql.Identifier(role)
    database = sql.Identifier(_fetch_one(cursor, "SELECT current_database()")[0])
    statements = (
        "GRANT CONNECT ON DATABASE {database} TO {role}",
        "GRANT USAGE ON SCHEMA public TO {role}",
        "GRANT {tables} ON ALL TABLES IN SCHEMA public TO {role}",
        "GRANT {sequences} ON ALL SEQUENCES IN SCHEMA public TO {role}",
        "ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public GRANT {tables} ON TABLES TO {role}",
        "ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public GRANT {sequences} ON SEQUENCES TO {role}",
    )
    for statement in statements:
        cursor.execute(
            sql.SQL(statement).format(
                database=database, role=app_role, tables=TABLE_PRIVILEGES, sequences=SEQUENCE_PRIVILEGES
            )
        )


def _missing_password_result(context: HousekeepingContext) -> StepResult:
    if context.password_required:
        raise ValueError("APP_DB_PASSWORD must be set to manage the application role")
    return StepResult("roles", False, "skipped: APP_DB_PASSWORD is not set")


def ensure_roles(context: HousekeepingContext) -> StepResult:
    if not context.app_password:
        return _missing_password_result(context)
    with _autocommit_cursor(context.database_url) as cursor:
        exists = _role_exists(cursor, context.app_role)
        if context.dry_run:
            return StepResult("roles", not exists, f"would sync role {context.app_role}")
        _upsert_login_role(cursor, context.app_role, context.app_password, exists)
        _grant_data_access(cursor, context.app_role)
    return StepResult("roles", not exists, f"synced role {context.app_role}")


def _collation_versions(cursor) -> tuple[Optional[str], Optional[str]]:
    return _fetch_one(
        cursor,
        "SELECT datcollversion, pg_database_collation_actual_version(oid) "
        "FROM pg_database WHERE datname = current_database()",
    )


def _refresh_collation(cursor, reindex: bool) -> None:
    database = sql.Identifier(_fetch_one(cursor, "SELECT current_database()")[0])
    cursor.execute(sql.SQL("ALTER DATABASE {} REFRESH COLLATION VERSION").format(database))
    if reindex:
        cursor.execute(sql.SQL("REINDEX DATABASE {}").format(database))
        return
    logger.warning("collation_version_refreshed: run housekeeping with --reindex to rebuild text indexes")


def refresh_collation(context: HousekeepingContext) -> StepResult:
    with _autocommit_cursor(context.database_url) as cursor:
        recorded, actual = _collation_versions(cursor)
        if recorded == actual:
            return StepResult("collation", False, "collation version matches the operating system")
        if context.dry_run:
            return StepResult("collation", True, f"would refresh {recorded} -> {actual}")
        _refresh_collation(cursor, context.reindex)
    return StepResult("collation", True, f"refreshed {recorded} -> {actual}")


STEPS: dict[str, Callable[[HousekeepingContext], StepResult]] = {
    "extensions": ensure_extensions,
    "migrate": migrate_schema,
    "roles": ensure_roles,
    "collation": refresh_collation,
}


def run_housekeeping(context: HousekeepingContext, step: str = "all") -> list[StepResult]:
    names = STEP_NAMES if step == "all" else (step,)
    results = [STEPS[name](context) for name in names]
    logger.info(
        "housekeeping_completed %s",
        json.dumps({"dry_run": context.dry_run, "steps": [asdict(result) for result in results]}),
    )
    return results


def context_from_settings(settings: Settings, dry_run: bool, reindex: bool) -> HousekeepingContext:
    return HousekeepingContext(
        database_url=settings.HOUSEKEEPING_DATABASE_URL or settings.DATABASE_URL,
        app_role=settings.APP_DB_ROLE,
        app_password=settings.APP_DB_PASSWORD,
        password_required=settings.APP_ENV == "cloud",
        dry_run=dry_run,
        reindex=reindex,
    )


def _parse_arguments(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run privileged database housekeeping.")
    parser.add_argument("--step", choices=("all", *STEP_NAMES), default="all")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without changing it.")
    parser.add_argument("--reindex", action="store_true", help="REINDEX after a collation refresh.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    arguments = _parse_arguments(argv)
    context = context_from_settings(get_settings(), arguments.dry_run, arguments.reindex)
    run_housekeeping(context, arguments.step)
    return 0


if __name__ == "__main__":
    sys.exit(main())
