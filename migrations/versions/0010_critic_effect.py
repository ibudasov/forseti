from __future__ import annotations

"""add critic effect to agent runs.

Revision ID: 0010_critic_effect
Revises: 0009_chunk_source_metadata
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_critic_effect"
down_revision = "0009_chunk_source_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("critic_effect", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "critic_effect")
