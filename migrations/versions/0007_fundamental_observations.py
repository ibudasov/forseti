from __future__ import annotations

"""add fundamental observations.

Revision ID: 0007_fundamental_observations
Revises: 7d97d375ff32
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_fundamental_observations"
down_revision = "7d97d375ff32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fundamental_observation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=4), nullable=False),
        sa.Column("form_type", sa.String(length=8), nullable=False),
        sa.Column("filed_at", sa.Date(), nullable=True),
        sa.Column("accession_number", sa.String(length=32), nullable=True),
        sa.Column("source_concept", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("is_derived", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("derivation", sa.Text(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["security_id"], ["security.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "security_id",
            "metric_name",
            "period_start",
            "period_end",
            "fiscal_period",
            "form_type",
            "unit",
            "source_concept",
            "accession_number",
            "is_derived",
            name="uq_fundamental_observation_ingest",
        ),
    )
    op.create_index(
        "ix_fundamental_observation_metric_period",
        "fundamental_observation",
        ["security_id", "metric_name", "period_end"],
        unique=False,
    )
    op.create_index(
        "ix_fundamental_observation_metric_fiscal_period",
        "fundamental_observation",
        ["security_id", "metric_name", "fiscal_period", "period_end"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_fundamental_observation_metric_fiscal_period", table_name="fundamental_observation")
    op.drop_index("ix_fundamental_observation_metric_period", table_name="fundamental_observation")
    op.drop_table("fundamental_observation")
