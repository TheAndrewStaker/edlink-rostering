"""Alembic environment.

Reads the migration target URL from `MIGRATION_DATABASE_URL` so migrations run
under a privileged account (edlink_dba in production, the local superuser
locally) without baking that URL into the file.

Imports the SQLAlchemy metadata so `alembic revision --autogenerate` can detect
model changes. The initial migration is hand-written; autogenerate is a
diff helper for subsequent migrations.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from edlink_rostering.infrastructure.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _migration_url() -> str:
    url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Set MIGRATION_DATABASE_URL (or DATABASE_URL) to a Postgres async URL "
            "for the migration target."
        )
    return url


config.set_main_option("sqlalchemy.url", _migration_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # psycopg async refuses ProactorEventLoop on Windows. Passing
    # SelectorEventLoop as loop_factory is the Python 3.12+ replacement
    # for the deprecated set_event_loop_policy + WindowsSelectorEventLoopPolicy
    # pair (both slated for removal in 3.16). SelectorEventLoop is
    # cross-platform; it's the default on Unix and the working override
    # on Windows.
    asyncio.run(
        run_migrations_online(),
        loop_factory=asyncio.SelectorEventLoop,
    )
