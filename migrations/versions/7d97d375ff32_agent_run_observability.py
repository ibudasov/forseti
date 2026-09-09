"""agent_run_observability

Revision ID: 7d97d375ff32
Revises: 0006_trace_observability
Create Date: 2026-09-09 15:18:32.124814
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '7d97d375ff32'
down_revision = '0006_trace_observability'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agent_runs', sa.Column('entered_agent_layer', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False))
    op.add_column('agent_runs', sa.Column('adk_event_count', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('agent_runs', sa.Column('observed_agents', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade() -> None:
    op.drop_column('agent_runs', 'observed_agents')
    op.drop_column('agent_runs', 'adk_event_count')
    op.drop_column('agent_runs', 'entered_agent_layer')
