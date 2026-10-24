from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text


def test_agent_run_as_of_migration_backfills_and_downgrades(db_engine):
    migration = import_module("migrations.versions.0012_agent_run_as_of_date")
    with db_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS agent_run_steps CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS agent_runs CASCADE"))
        connection.execute(
            text(
                "CREATE TABLE agent_runs ("
                "run_id VARCHAR(36) PRIMARY KEY, "
                "ticker VARCHAR(16) NOT NULL, "
                "created_at TIMESTAMPTZ NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (run_id, ticker, created_at) "
                "VALUES ('existing-run', 'NVDA', '2024-01-02T23:30:00+00:00')"
            )
        )

        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

        row = connection.execute(
            text(
                "SELECT as_of_date, as_of_date_source "
                "FROM agent_runs WHERE run_id = 'existing-run'"
            )
        ).one()
        assert str(row.as_of_date) == "2024-01-02"
        assert row.as_of_date_source == "created_at_backfill"
        assert "ix_agent_runs_ticker_as_of_date" in {
            index["name"] for index in inspect(connection).get_indexes("agent_runs")
        }

        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

        assert {column["name"] for column in inspect(connection).get_columns("agent_runs")} == {
            "run_id",
            "ticker",
            "created_at",
        }
