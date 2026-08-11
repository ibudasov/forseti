from __future__ import annotations

import os
import sys

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
from sqlmodel import SQLModel

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.settings import get_settings

config = context.config
fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Pre-migration setup: Create extensions and refresh collation
        # These need to be done outside the transaction
        try:
            connection.connection.autocommit = True
            cursor = connection.connection.cursor()
            
            # Create pgvector extension if it doesn't exist
            try:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception as e:
                print(f"Note: pgvector extension may already exist or couldn't be created: {e}")
            
            # Refresh collation version to match the operating system's collation library
            # This resolves "has a collation version mismatch" warnings
            try:
                db_name = settings.DATABASE_URL.split("/")[-1]
                cursor.execute(f"ALTER DATABASE {db_name} REFRESH COLLATION VERSION;")
                print(f"Refreshed collation version for database '{db_name}'")
            except Exception as e:
                print(f"Note: Could not refresh collation version: {e}")
            
            cursor.close()
            connection.connection.autocommit = False
        except Exception as e:
            print(f"Note: Error during pre-migration setup: {e}")
        
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
