"""persist the analysis date for agent runs

Revision ID: 0012_agent_run_as_of_date
Revises: 0011_technical_agent_effect
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012_agent_run_as_of_date"
down_revision = "0011_technical_agent_effect"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("as_of_date", sa.Date(), nullable=True))
    op.add_column("agent_runs", sa.Column("as_of_date_source", sa.String(length=32), nullable=True))
    op.execute(
        sa.text(
            "UPDATE agent_runs "
            "SET as_of_date = created_at::date, as_of_date_source = 'created_at_backfill'"
        )
    )
    op.create_index(
        "ix_agent_runs_ticker_as_of_date",
        "agent_runs",
        ["ticker", "as_of_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_ticker_as_of_date", table_name="agent_runs")
    op.drop_column("agent_runs", "as_of_date_source")
    op.drop_column("agent_runs", "as_of_date")
