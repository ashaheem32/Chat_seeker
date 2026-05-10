"""
Async SQLAlchemy engine + session factory.

Design notes:
- We use the async engine because FastAPI is async-first and asyncpg gives us
  the best Postgres driver performance.
- A single engine + sessionmaker is created at import time; this is fine
  because engines are lightweight wrappers that lazily open connections.
- get_db() is the FastAPI dependency. It yields an AsyncSession that is
  automatically closed (and rolled back on exception) at the end of the
  request via the async context manager.
- Schema changes go through Alembic. create_all_tables() exists ONLY for
  tests / first-boot dev convenience — never call it in production.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    """Shared declarative base. All ORM models must inherit from this."""

    pass


def _create_engine() -> AsyncEngine:
    """Build the async engine with sane production defaults."""
    return create_async_engine(
        settings.DATABASE_URL,
        # echo SQL in dev only - way too noisy in prod
        echo=settings.is_development and settings.LOG_LEVEL == "DEBUG",
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        # asyncpg-specific: disable JIT for more predictable query perf on
        # workloads dominated by short queries (FastAPI request handlers).
        connect_args={"server_settings": {"jit": "off"}},
    )


# Module-level singletons. FastAPI lifespan disposes the engine on shutdown.
engine: AsyncEngine = _create_engine()

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep ORM objects usable after commit (common FastAPI pattern)
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session per request.

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        # Note: we don't auto-commit. Routes commit explicitly so partial
        # writes don't leak when the route raises after some flushes.


@asynccontextmanager
async def lifespan_db() -> AsyncGenerator[None, None]:
    """
    Engine lifecycle manager for FastAPI's `lifespan` parameter.

    Disposes the connection pool on app shutdown so we don't leak DB
    connections between hot reloads or graceful restarts.
    """
    try:
        yield
    finally:
        await engine.dispose()


def make_worker_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build a throwaway engine + sessionmaker for a single Celery task.

    Why: the module-level `engine` binds its asyncpg connection pool to
    the loop that first opened a connection. Celery prefork workers run
    each task with `asyncio.run(...)`, which creates a fresh loop, so
    reusing the global pool raises "Future attached to a different loop".
    NullPool sidesteps the issue: no pooling, fresh connection per task.

    The caller is responsible for `await engine.dispose()` in a finally.
    """
    eng = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        connect_args={"server_settings": {"jit": "off"}},
    )
    sm = async_sessionmaker(
        bind=eng,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return eng, sm


async def create_all_tables() -> None:
    """
    Create all tables defined on Base.metadata.

    Intended for tests and first-time local dev only — production schema
    must go through Alembic. Importing app.models here ensures every model
    is registered on Base.metadata before create_all runs.
    """
    # Local import to dodge circular deps at module load time.
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        # pgvector / pg_trgm / citext are enabled in scripts/init-db.sql, but
        # repeat them here so create_all_tables works against a bare DB too.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(Base.metadata.create_all)


async def drop_all_tables() -> None:
    """Drop everything. Tests only — guarded against production accidents."""
    if settings.is_production:
        raise RuntimeError("Refusing to drop tables in production")

    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
