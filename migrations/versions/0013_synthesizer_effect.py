from __future__ import annotations

"""add synthesizer effect to agent runs.

Revision ID: 0013_synthesizer_effect
Revises: 0012_agent_run_as_of_date
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_synthesizer_effect"
down_revision = "0012_agent_run_as_of_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("synthesizer_effect", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "synthesizer_effect")
