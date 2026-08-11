"""refresh collation version

Revision ID: 0004_refresh_collation_version
Revises: 0003_document_chunks
Create Date: 2026-08-11 00:00:00.000000

This migration refreshes the database collation version to match the
operating system's collation library. This resolves warnings like:
  "database has a collation version mismatch"

The fix is applied in two places:
1. In migrations/env.py - runs automatically during `alembic upgrade`
2. In app/db/session.py - runs on each new database connection at runtime

No SQL changes are needed here; the actual refresh is handled by the
pre-migration setup in env.py and connection event listeners.
"""
from alembic import op

revision = "0004_refresh_collation_version"
down_revision = "0003_document_chunks"
branch_labels = None
dependencies = None


def upgrade() -> None:
    # Collation refresh is handled in migrations/env.py before migrations run
    # This migration just acts as a checkpoint marker in the migration history
    pass


def downgrade() -> None:
    # No action needed - collation version is a database state, not a schema change
    pass
