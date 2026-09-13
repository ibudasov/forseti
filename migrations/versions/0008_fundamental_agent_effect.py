from __future__ import annotations

"""add fundamental agent effect to agent runs.

Revision ID: 0008_fundamental_agent_effect
Revises: 0007_fundamental_observations
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_fundamental_agent_effect"
down_revision = "0007_fundamental_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("fundamental_agent_effect", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "fundamental_agent_effect")
