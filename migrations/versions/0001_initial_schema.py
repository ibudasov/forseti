"""initial schema

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-08-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.create_table(
        "security",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(length=16), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("sector_tag", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.CheckConstraint("sector_tag IN ('ai','defence','nuclear','green_energy','quantum','robotics','space')", name="ck_security_sector_tag"),
    )

    op.create_table(
        "price_bar",
        sa.Column("security_id", sa.Integer(), sa.ForeignKey("security.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("bar_date", sa.Date(), primary_key=True, nullable=False),
        sa.Column("open", sa.Numeric(14, 4), nullable=False),
        sa.Column("high", sa.Numeric(14, 4), nullable=False),
        sa.Column("low", sa.Numeric(14, 4), nullable=False),
        sa.Column("close", sa.Numeric(14, 4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_price_bar_security_date_desc", "price_bar", ["security_id", sa.text("bar_date DESC")])

    op.create_table(
        "fundamental",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("security_id", sa.Integer(), sa.ForeignKey("security.id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("revenue_growth", sa.Numeric(12, 6), nullable=True),
        sa.Column("fcf", sa.Numeric(18, 4), nullable=True),
        sa.Column("debt_to_equity", sa.Numeric(12, 6), nullable=True),
        sa.Column("eps_trend", sa.Numeric(12, 6), nullable=True),
        sa.Column("margins", sa.Numeric(12, 6), nullable=True),
        sa.Column("raw_payload", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("timezone('utc', now())")),
    )

    op.create_table(
        "earnings_event",
        sa.Column("security_id", sa.Integer(), sa.ForeignKey("security.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("report_date", sa.Date(), primary_key=True, nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
    )

    op.create_table(
        "macro_daily",
        sa.Column("obs_date", sa.Date(), primary_key=True, nullable=False),
        sa.Column("vix", sa.Numeric(8, 3), nullable=True),
    )

    op.create_table(
        "technical_feature",
        sa.Column("security_id", sa.Integer(), sa.ForeignKey("security.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("as_of_date", sa.Date(), primary_key=True, nullable=False),
        sa.Column("rsi_14", sa.Numeric(7, 4), nullable=True),
        sa.Column("sma_50", sa.Numeric(14, 4), nullable=True),
        sa.Column("sma_200", sa.Numeric(14, 4), nullable=True),
        sa.Column("volume_trend", sa.Numeric(14, 6), nullable=True),
    )

    op.create_table(
        "recommendation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("security_id", sa.Integer(), sa.ForeignKey("security.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("entry_low", sa.Numeric(14, 4), nullable=True),
        sa.Column("entry_high", sa.Numeric(14, 4), nullable=True),
        sa.Column("stop_loss", sa.Numeric(14, 4), nullable=True),
        sa.Column("take_profit_1", sa.Numeric(14, 4), nullable=True),
        sa.Column("take_profit_2", sa.Numeric(14, 4), nullable=True),
        sa.Column("risk_reward", sa.Numeric(10, 4), nullable=True),
        sa.Column("position_size", sa.Numeric(10, 4), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("reasons", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("full_payload", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.CheckConstraint("decision IN ('trade','watchlist','no_trade')", name="ck_recommendation_decision"),
    )
    op.create_index("ix_recommendation_created_at", "recommendation", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_recommendation_created_at", table_name="recommendation")
    op.drop_table("recommendation")
    op.drop_table("technical_feature")
    op.drop_table("macro_daily")
    op.drop_table("earnings_event")
    op.drop_table("fundamental")
    op.drop_index("ix_price_bar_security_date_desc", table_name="price_bar")
    op.drop_table("price_bar")
    op.drop_table("security")
