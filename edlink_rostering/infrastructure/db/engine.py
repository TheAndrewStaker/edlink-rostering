"""SQLAlchemy async engine and session factories.

Two role-scoped engines: the sync worker connects as edlink_app; the operator
CLI connects as edlink_ops. edlink_dba is reserved for retention and
break-glass and is not exposed from the application code path.

Connection URLs are resolved via :mod:`edlink_rostering.core.settings` from
``APP_DATABASE_URL`` (sync worker) and ``OPS_DATABASE_URL`` (CLI / HTTP).
``DATABASE_URL`` is the legacy fallback for single-role dev databases.
Engines are created lazily on first use so importing the module does not
require env vars (Alembic env.py imports models without using engines).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from edlink_rostering.core.settings import get_settings


@lru_cache(maxsize=1)
def app_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().app_db_url(), echo=False, pool_pre_ping=True
    )


@lru_cache(maxsize=1)
def ops_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().ops_db_url(), echo=False, pool_pre_ping=True
    )


@lru_cache(maxsize=1)
def app_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=app_engine(), expire_on_commit=False, class_=AsyncSession)


@lru_cache(maxsize=1)
def ops_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=ops_engine(), expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def app_session() -> AsyncIterator[AsyncSession]:
    """One-shot session for the sync worker. Commits on success, rolls back
    on exception. The sync worker uses this as its outer transactional
    boundary: one LEA-scoped batch equals one app_session block."""

    async with app_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def ops_session() -> AsyncIterator[AsyncSession]:
    """One-shot session for the CLI path. Same commit-or-rollback semantics."""

    async with ops_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
