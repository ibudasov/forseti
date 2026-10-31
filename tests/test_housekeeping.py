"""Housekeeping splits database rights: the owner changes schema, forseti_app only reads and writes."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlmodel import Session, select
from testcontainers.community.postgres import PostgresContainer

from agents.config import LINEAR_PIPELINE_MODE, AgentWorkflowConfig
from agents.orchestration.workflow import AgenticAnalysisWorkflow, load_trace
from app.db.models import DocumentChunk, PriceBar, Security, SourceQualityTier, SourceType
from app.db.repository import upsert_document_chunks, upsert_price_bars
from app.ingestion.universe import seed_universe
from app.jobs import housekeeping
from app.jobs.housekeeping import HousekeepingContext, context_from_settings
from app.schemas.analyze import AnalysisTrace
from app.settings import Settings
from tests.conftest import PGVECTOR_IMAGE

APP_ROLE = "forseti_app_housekeeping_test"
APP_PASSWORD = "app-password-for-tests"
DATABASE_NAME = "housekeeping_test"
DRY_RUN_DATABASE_NAME = "housekeeping_dry_run_test"
DRY_RUN_ROLE = "forseti_app_dry_run_test"


@contextmanager
def _server_url():
    configured_url = os.getenv("TEST_DATABASE_URL")
    if configured_url:
        yield configured_url
        return
    with PostgresContainer(PGVECTOR_IMAGE) as postgres:
        yield postgres.get_connection_url()


@contextmanager
def _disposable_database(name: str, owned_role: str | None = None):
    with _server_url() as server_url:
        server = make_url(server_url)
        maintenance = create_engine(server.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with maintenance.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            yield server.set(database=name).render_as_string(hide_password=False)
        finally:
            with maintenance.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                if owned_role:
                    # Roles are cluster-wide and can only go once the database holding their privileges is gone.
                    connection.execute(text(f'DROP ROLE IF EXISTS "{owned_role}"'))
            maintenance.dispose()


@pytest.fixture(scope="module")
def owner_url():
    with _disposable_database(DATABASE_NAME, owned_role=APP_ROLE) as url:
        yield url


@pytest.fixture(scope="module")
def housekeeping_context(owner_url):
    return HousekeepingContext(
        database_url=owner_url, app_role=APP_ROLE, app_password=APP_PASSWORD, password_required=True
    )


@pytest.fixture(scope="module")
def first_run(housekeeping_context):
    return housekeeping.run_housekeeping(housekeeping_context)


@pytest.fixture(scope="module")
def owner_engine(owner_url, first_run):
    engine = create_engine(owner_url, isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def app_engine(owner_url, first_run):
    url = make_url(owner_url).set(username=APP_ROLE, password=APP_PASSWORD)
    engine = create_engine(url)
    yield engine
    engine.dispose()


def _snapshot(owner_engine) -> dict:
    with owner_engine.connect() as connection:
        roles = connection.execute(
            text("SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles ORDER BY 1")
        ).all()
        acls = connection.execute(
            text("SELECT relname, relacl::text FROM pg_class WHERE relnamespace = 'public'::regnamespace ORDER BY 1")
        ).all()
        defaults = connection.execute(text("SELECT defaclacl::text FROM pg_default_acl ORDER BY 1")).all()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return {"roles": roles, "acls": acls, "defaults": defaults, "revision": revision}


def test_first_run_installs_extension_migrates_and_creates_the_application_role(first_run, owner_engine):
    assert [(result.step, result.changed) for result in first_run] == [
        ("extensions", True), ("migrate", True), ("roles", True), ("collation", False),
    ]
    with owner_engine.connect() as connection:
        role = connection.execute(
            text("SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin FROM pg_roles WHERE rolname = :role"),
            {"role": APP_ROLE},
        ).one()
        memberships = connection.execute(
            text("SELECT count(*) FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles WHERE rolname = :role)"),
            {"role": APP_ROLE},
        ).scalar()
    assert tuple(role) == (False, False, False, True)
    assert memberships == 0


def test_second_run_changes_nothing(housekeeping_context, first_run, owner_engine):
    before = _snapshot(owner_engine)

    results = housekeeping.run_housekeeping(housekeeping_context)

    assert not any(result.changed for result in results)
    assert _snapshot(owner_engine) == before


def test_dry_run_reports_pending_work_without_applying_it():
    with _disposable_database(DRY_RUN_DATABASE_NAME) as url:
        context = HousekeepingContext(
            database_url=url, app_role=DRY_RUN_ROLE, app_password=APP_PASSWORD, password_required=True, dry_run=True
        )
        engine = create_engine(url)

        results = housekeeping.run_housekeeping(context)

        with engine.connect() as connection:
            extensions = connection.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")).scalar()
            roles = connection.execute(
                text("SELECT count(*) FROM pg_roles WHERE rolname = :r"), {"r": DRY_RUN_ROLE}
            ).scalar()
        engine.dispose()
    assert [result.changed for result in results[:3]] == [True, True, True]
    assert (extensions, roles) == (0, 0)


def test_application_role_can_read_and_write_every_table(app_engine, owner_engine):
    with owner_engine.connect() as connection:
        tables = [row[0] for row in connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))]
        missing = [
            table for table in tables
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
            if not connection.execute(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": APP_ROLE, "table": f"public.{table}", "privilege": privilege},
            ).scalar()
        ]
        unwanted = connection.execute(
            text("SELECT has_table_privilege(:role, 'public.security', 'TRUNCATE')"), {"role": APP_ROLE}
        ).scalar()
    assert tables
    assert missing == []
    assert unwanted is False


def test_tables_created_after_the_grants_are_covered_by_default_privileges(app_engine, owner_engine):
    with owner_engine.connect() as connection:
        connection.execute(text("CREATE TABLE later_migration_table (id serial PRIMARY KEY, label text)"))

    with app_engine.begin() as connection:
        connection.execute(text("INSERT INTO later_migration_table (label) VALUES ('written')"))
        connection.execute(text("UPDATE later_migration_table SET label = 'updated'"))
        label = connection.execute(text("SELECT label FROM later_migration_table")).scalar()
        connection.execute(text("DELETE FROM later_migration_table"))
    assert label == "updated"


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE forbidden_table (id int)",
        "DROP TABLE security CASCADE",
        "ALTER TABLE security ADD COLUMN forbidden text",
        "ALTER DATABASE DATABASE_NAME REFRESH COLLATION VERSION",
        "CREATE ROLE forbidden_role",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    ],
)
def test_application_role_cannot_change_schema_or_database(app_engine, statement):
    statement = statement.replace("DATABASE_NAME", DATABASE_NAME)
    with pytest.raises(ProgrammingError, match="permission denied|must be owner"):
        with app_engine.begin() as connection:
            connection.execute(text(statement))


def test_application_role_runs_ingestion_and_trace_persistence(app_engine):
    inserted = seed_universe(engine=app_engine)
    with Session(app_engine) as session:
        security = session.exec(select(Security).where(Security.ticker == "NVDA")).one()
    upsert_price_bars(
        [
            PriceBar(
                security_id=security.id, bar_date=date(2026, 1, 2),
                open=Decimal(1), high=Decimal(2), low=Decimal(1), close=Decimal(2), volume=10,
            )
        ],
        engine=app_engine,
    )
    upsert_document_chunks([_chunk()], engine=app_engine)
    workflow = AgenticAnalysisWorkflow(
        AgentWorkflowConfig(
            pipeline_mode=LINEAR_PIPELINE_MODE, model_name="test-model", temperature=0.2,
            timeout_seconds=30, max_retries=1,
        ),
        engine=app_engine,
    )

    workflow._persist_trace(AnalysisTrace(run_id="housekeeping-run", ticker="NVDA"))

    assert inserted > 0
    assert load_trace("housekeeping-run", engine=app_engine).ticker == "NVDA"
    with app_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM price_bar")).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM document_chunk")).scalar() == 1


def _chunk() -> DocumentChunk:
    return DocumentChunk(
        ticker="NVDA", source_type=SourceType.filing_business, document_id="sec:0001",
        source_url="https://example.com/10k", publisher="Example Corp", title="NVDA 10-K",
        source_quality_tier=SourceQualityTier.primary_regulatory, source_hash="housekeeping-hash",
        published_at=datetime(2026, 2, 1, tzinfo=timezone.utc), chunk_index=0, text="Business text",
        embedding=[0.0] * 768,
    )


def test_collation_mismatch_is_refreshed_and_reindexed_only_on_request(housekeeping_context, owner_engine):
    with owner_engine.connect() as connection:
        connection.execute(text("UPDATE pg_database SET datcollversion = '0.0' WHERE datname = current_database()"))

    dry_run = housekeeping.run_housekeeping(
        HousekeepingContext(**{**housekeeping_context.__dict__, "dry_run": True}), "collation"
    )
    refreshed = housekeeping.run_housekeeping(housekeeping_context, "collation")
    unchanged = housekeeping.run_housekeeping(housekeeping_context, "collation")

    assert [dry_run[0].changed, refreshed[0].changed, unchanged[0].changed] == [True, True, False]
    assert "would refresh" in dry_run[0].detail


def test_password_is_required_in_the_cloud_but_optional_locally():
    local = HousekeepingContext("postgresql://unused/db", APP_ROLE, None, password_required=False)
    cloud = HousekeepingContext("postgresql://unused/db", APP_ROLE, None, password_required=True)

    assert housekeeping.ensure_roles(local).changed is False
    with pytest.raises(ValueError, match="APP_DB_PASSWORD"):
        housekeeping.ensure_roles(cloud)


def test_context_prefers_the_housekeeping_url_and_marks_cloud_password_required():
    settings = Settings(
        _env_file=None, APP_ENV="cloud", DATABASE_URL="postgresql://app/db",
        HOUSEKEEPING_DATABASE_URL="postgresql://owner/db", APP_DB_PASSWORD="secret",
    )

    context = context_from_settings(settings, dry_run=True, reindex=False)

    assert (context.database_url, context.password_required, context.dry_run) == ("postgresql://owner/db", True, True)


def test_context_falls_back_to_the_database_url_locally():
    settings = Settings(_env_file=None, DATABASE_URL="postgresql://local/db")

    context = context_from_settings(settings, dry_run=False, reindex=False)

    assert (context.database_url, context.app_role, context.password_required) == (
        "postgresql://local/db", "forseti_app", False,
    )


def test_the_password_never_appears_in_the_completion_log(housekeeping_context, caplog):
    with caplog.at_level("INFO"):
        housekeeping.run_housekeeping(housekeeping_context, "roles")

    assert "housekeeping_completed" in caplog.text
    assert APP_PASSWORD not in caplog.text
