"""persist the OpenTelemetry trace identifier on agent runs.

Revision ID: 0015_agent_run_trace_id
Revises: 0014_agent_run_cost
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_agent_run_trace_id"
down_revision = "0014_agent_run_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("trace_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "trace_id")
