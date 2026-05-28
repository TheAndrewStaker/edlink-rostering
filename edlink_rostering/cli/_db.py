"""Shared DB session factory for CLI commands.

Each command opens its own session through :func:`session_factory`. The
factory honors ``OPS_DATABASE_URL`` (falling back to ``DATABASE_URL``)
and caches a single engine across the process so repeated commands do
not pay engine-construction cost.
"""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _url() -> str:
    value = (
        os.environ.get("OPS_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not value:
        raise click_url_error()
    return value


def click_url_error() -> RuntimeError:
    """Raised when neither URL env var is set. Click renders the
    runtime error as a clean CLI error."""

    return RuntimeError(
        "OPS_DATABASE_URL (or DATABASE_URL) must be set to a Postgres "
        "async URL. Example: "
        "postgresql+psycopg://edlink_ops:pass@localhost:5433/edlink_poc"
    )


@lru_cache(maxsize=1)
def session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(_url(), echo=False, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
