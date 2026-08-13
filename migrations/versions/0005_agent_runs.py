"""add persisted agent workflow traces

Revision ID: 0005_agent_runs
Revises: 0004_refresh_collation_version
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_agent_runs"
down_revision = "0004_refresh_collation_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("agent_runs"):
        op.create_table(
            "agent_runs",
            sa.Column("run_id", sa.String(length=36), primary_key=True),
            sa.Column("ticker", sa.String(length=16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("final_decision", sa.String(length=16), nullable=True),
            sa.Column("total_latency_ms", sa.Float(), nullable=False),
            sa.Column("token_usage", postgresql.JSONB(), nullable=False),
            sa.Column("warnings", postgresql.JSONB(), nullable=False),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_runs_ticker ON agent_runs (ticker)")
    if not inspector.has_table("agent_run_steps"):
        op.create_table(
            "agent_run_steps",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("run_id", sa.String(length=36), sa.ForeignKey("agent_runs.run_id"), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("agent_name", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("tool_calls", postgresql.JSONB(), nullable=False),
            sa.Column("latency_ms", sa.Float(), nullable=False),
            sa.Column("token_usage", postgresql.JSONB(), nullable=False),
            sa.Column("retries", sa.Integer(), nullable=False),
            sa.Column("output", postgresql.JSONB(), nullable=True),
            sa.UniqueConstraint("run_id", "sequence", name="uq_agent_run_steps_order"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_run_steps_run_id ON agent_run_steps (run_id)")


def downgrade() -> None:
    op.drop_index("ix_agent_run_steps_run_id", table_name="agent_run_steps")
    op.drop_table("agent_run_steps")
    op.drop_index("ix_agent_runs_ticker", table_name="agent_runs")
    op.drop_table("agent_runs")