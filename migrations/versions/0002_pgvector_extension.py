"""pgvector extension stub

Revision ID: 0002_pgvector_extension
Revises: 0001_initial_schema
Create Date: 2026-08-05 00:00:00.000000
"""
from alembic import op

revision = "0002_pgvector_extension"
down_revision = "0001_initial_schema"
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector;")
