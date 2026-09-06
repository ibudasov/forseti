"""store trace step skip reasons.

Revision ID: 0006_trace_observability
Revises: 0005_agent_runs
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_trace_observability"
down_revision = "0005_agent_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_run_steps", sa.Column("skip_reason", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_run_steps", "skip_reason")
