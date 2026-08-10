"""pgvector extension stub

Revision ID: 0002_pgvector_extension
Revises: 0001_initial_schema
Create Date: 2026-08-05 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0002_pgvector_extension"
down_revision = "0001_initial_schema"
branch_labels = None
dependencies = None


def upgrade() -> None:
    # The pgvector extension is created in env.py before migrations start
    # This migration just acts as a marker in the migration history
    pass


def downgrade() -> None:
    pass
