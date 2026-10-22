from __future__ import annotations

"""add technical agent effect to agent runs.

Revision ID: 0011_technical_agent_effect
Revises: 0010_critic_effect
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_technical_agent_effect"
down_revision = "0010_critic_effect"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("technical_agent_effect", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "technical_agent_effect")
