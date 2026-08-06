"""create tickers table

Revision ID: 0001_create_tickers_table
Revises:
Create Date: 2026-08-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_create_tickers_table"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tickers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("symbol", sa.String(12), nullable=False, unique=True),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("sector", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column(
            "tracked_since",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "sector IN ('ai','defence','nuclear','green_energy','quantum','robotics','space')",
            name="ck_tickers_sector",
        ),
    )


def downgrade() -> None:
    op.drop_table("tickers")
