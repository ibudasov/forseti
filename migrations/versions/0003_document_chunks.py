"""document_chunks table for RAG vector storage

Revision ID: 0003_document_chunks
Revises: 0002_pgvector_extension
Create Date: 2026-08-10 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_document_chunks"
down_revision = "0002_pgvector_extension"
branch_labels = None
dependencies = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.create_table(
        "document_chunk",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "embedding",
            sa.Text,
            nullable=True,
            comment=f"Stored as vector({EMBEDDING_DIM}) via pgvector",
        ),
    )
    # Convert to pgvector column after table creation
    op.execute(f"ALTER TABLE document_chunk ALTER COLUMN embedding TYPE vector({EMBEDDING_DIM}) USING NULL")

    op.create_index("ix_document_chunk_ticker_source_type", "document_chunk", ["ticker", "source_type"])
    op.create_index("ix_document_chunk_published_at", "document_chunk", ["published_at"])
    op.create_unique_constraint("uq_document_chunk_source_hash", "document_chunk", ["source_hash"])
    # HNSW index for fast cosine similarity search
    op.execute(
        "CREATE INDEX ix_document_chunk_embedding_hnsw "
        "ON document_chunk USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("document_chunk")
