"""seed reference universe

Revision ID: 0002_seed_reference_universe
Revises: 0001_create_tickers_table
Create Date: 2026-08-06 00:00:01.000000
"""

from alembic import op

revision = "0002_seed_reference_universe"
down_revision = "0001_create_tickers_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO tickers (id, symbol, exchange, company_name, sector) VALUES
            ('00000000-0000-0000-0000-000000000001', 'NVDA', 'NASDAQ', 'NVIDIA Corporation', 'ai'),
            ('00000000-0000-0000-0000-000000000002', 'PLTR', 'NASDAQ', 'Palantir Technologies Inc.', 'ai'),
            ('00000000-0000-0000-0000-000000000003', 'LMT', 'NYSE', 'Lockheed Martin Corporation', 'defence'),
            ('00000000-0000-0000-0000-000000000004', 'RTX', 'NYSE', 'RTX Corporation', 'defence'),
            ('00000000-0000-0000-0000-000000000005', 'SMR', 'NYSE', 'NuScale Power Corporation', 'nuclear'),
            ('00000000-0000-0000-0000-000000000006', 'CCJ', 'NYSE', 'Cameco Corporation', 'nuclear'),
            ('00000000-0000-0000-0000-000000000007', 'ENPH', 'NASDAQ', 'Enphase Energy, Inc.', 'green_energy'),
            ('00000000-0000-0000-0000-000000000008', 'FSLR', 'NASDAQ', 'First Solar, Inc.', 'green_energy'),
            ('00000000-0000-0000-0000-000000000009', 'IONQ', 'NYSE', 'IonQ, Inc.', 'quantum'),
            ('00000000-0000-0000-0000-00000000000a', 'RGTI', 'NASDAQ', 'Rigetti Computing, Inc.', 'quantum'),
            ('00000000-0000-0000-0000-00000000000b', 'SYM', 'NASDAQ', 'Symbotic Inc.', 'robotics'),
            ('00000000-0000-0000-0000-00000000000c', 'TER', 'NASDAQ', 'Teradyne, Inc.', 'robotics'),
            ('00000000-0000-0000-0000-00000000000d', 'RKLB', 'NASDAQ', 'Rocket Lab USA, Inc.', 'space'),
            ('00000000-0000-0000-0000-00000000000e', 'PL', 'NYSE', 'Planet Labs PBC', 'space')
        ON CONFLICT (symbol) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM tickers
        WHERE symbol IN (
            'NVDA', 'PLTR', 'LMT', 'RTX', 'SMR', 'CCJ', 'ENPH', 'FSLR',
            'IONQ', 'RGTI', 'SYM', 'TER', 'RKLB', 'PL'
        )
        """
    )
