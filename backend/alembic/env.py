"""
Alembic environment.

Bridges Alembic (which is sync) with our async SQLAlchemy stack.
Key responsibilities:
- Pull DATABASE_URL from app.core.config.settings (single source of truth).
- Import every ORM model so Base.metadata is fully populated for autogenerate.
- Drive migrations through async_engine.run_sync(...) inside an asyncio loop.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# IMPORTANT: importing app.models registers every model on Base.metadata.
import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import Base

# Alembic Config object — reads alembic.ini.
config = context.config

# Inject the runtime DB URL. asyncpg-only URL works for both online migration
# (we use async_engine_from_config) and offline migration (Alembic just emits
# SQL without running it, so the dialect is what matters).
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Apply logging config from alembic.ini if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata target for --autogenerate.
target_metadata = Base.metadata


def _include_object(object_, name, type_, reflected, compare_to):  # noqa: ANN001
    """
    Filter for autogenerate.

    Skip the pgvector-internal `vector` table that the extension creates —
    Alembic would otherwise propose to drop it on every autogenerate.
    """
    if type_ == "table" and name in {"vector", "vector_ivfflat", "vector_hnsw"}:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL to stdout, no DB connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # short-lived migration script — no need to pool
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
