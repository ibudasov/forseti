from __future__ import annotations

"""expand document chunk source metadata.

Revision ID: 0009_document_chunk_source_metadata
Revises: 0008_fundamental_agent_effect
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_document_chunk_source_metadata"
down_revision = "0008_fundamental_agent_effect"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_chunk", sa.Column("document_id", sa.String(length=128), nullable=True))
    op.add_column("document_chunk", sa.Column("publisher", sa.String(length=255), nullable=True))
    op.add_column("document_chunk", sa.Column("title", sa.String(length=512), nullable=True))
    op.add_column("document_chunk", sa.Column("form_type", sa.String(length=16), nullable=True))
    op.add_column("document_chunk", sa.Column("accession_number", sa.String(length=32), nullable=True))
    op.add_column("document_chunk", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("document_chunk", sa.Column("period_end", sa.Date(), nullable=True))
    op.add_column("document_chunk", sa.Column("source_quality_tier", sa.String(length=32), nullable=True))

    op.execute(
        """
        UPDATE document_chunk
        SET source_type = CASE
            WHEN source_type = 'earnings_call' AND text LIKE 'Earnings calendar:%' THEN 'earnings_calendar'
            WHEN source_type = 'earnings_call' THEN 'analyst_recommendations'
            ELSE source_type
        END
        """
    )
    op.execute(
        """
        UPDATE document_chunk
        SET document_id = source_type || ':' || substring(source_hash from 1 for 16),
            publisher = CASE
                WHEN source_type IN ('filing_business', 'filing_risk', 'filing_mda', 'earnings_release') THEN ticker
                ELSE 'Unknown source'
            END,
            title = source_type || ' chunk ' || chunk_index,
            source_quality_tier = CASE
                WHEN source_type IN ('filing_business', 'filing_risk', 'filing_mda', 'earnings_release') THEN 'primary_regulatory'
                WHEN source_type = 'earnings_call_transcript' THEN 'unknown'
                ELSE 'secondary_reputable'
            END
        """
    )

    op.alter_column("document_chunk", "document_id", nullable=False)
    op.alter_column("document_chunk", "publisher", nullable=False)
    op.alter_column("document_chunk", "title", nullable=False)
    op.alter_column("document_chunk", "source_quality_tier", nullable=False)
    op.create_index("ix_document_chunk_document_id", "document_chunk", ["document_id"])
    op.create_index(
        "ix_document_chunk_ticker_quality_published",
        "document_chunk",
        ["ticker", "source_quality_tier", "published_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunk_ticker_quality_published", table_name="document_chunk")
    op.drop_index("ix_document_chunk_document_id", table_name="document_chunk")
    op.drop_column("document_chunk", "source_quality_tier")
    op.drop_column("document_chunk", "period_end")
    op.drop_column("document_chunk", "period_start")
    op.drop_column("document_chunk", "accession_number")
    op.drop_column("document_chunk", "form_type")
    op.drop_column("document_chunk", "title")
    op.drop_column("document_chunk", "publisher")
    op.drop_column("document_chunk", "document_id")
