"""document chunks table with pgvector embedding support

Revision ID: 0003_document_chunks
Revises: 0002_pgvector_extension
Create Date: 2026-08-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_document_chunks"
down_revision = "0002_pgvector_extension"
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.create_table(
        "document_chunk",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(length=16), nullable=False, index=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", sa.text(), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('filing_business','filing_risk','earnings_call','company_news','sector_news')",
            name="ck_document_chunk_source_type"
        ),
    )
    op.create_index("ix_document_chunk_ticker", "document_chunk", ["ticker"])
    op.create_index("ix_document_chunk_source_type", "document_chunk", ["source_type"])
    op.create_index("ix_document_chunk_ingested_at", "document_chunk", ["ingested_at"])
    op.create_index("ix_document_chunk_source_hash", "document_chunk", ["source_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_document_chunk_source_hash", table_name="document_chunk")
    op.drop_index("ix_document_chunk_ingested_at", table_name="document_chunk")
    op.drop_index("ix_document_chunk_source_type", table_name="document_chunk")
    op.drop_index("ix_document_chunk_ticker", table_name="document_chunk")
    op.drop_table("document_chunk")
