from __future__ import annotations

"""add estimated cost to agent runs.

Revision ID: 0014_agent_run_cost
Revises: 0013_synthesizer_effect
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_agent_run_cost"
down_revision = "0013_synthesizer_effect"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("cost_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("agent_runs", sa.Column("pricing_version", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "pricing_version")
    op.drop_column("agent_runs", "cost_breakdown")
    op.drop_column("agent_runs", "cost_usd")
