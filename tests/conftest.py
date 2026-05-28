"""Shared pytest fixtures.

Three responsibilities:

1. Make sure the asyncio event loop is the SelectorEventLoop on Windows. The
   psycopg async driver does not work on the default ProactorEventLoop; this
   would surface as an InterfaceError the first time any test touches the DB.
   Setting the policy here happens before pytest-asyncio constructs its loop.

2. Expose a fixtures directory helper and a DB session factory. The DB
   factory skips its tests if APP_DATABASE_URL is unset, so the DB-free
   tests still run in environments without Postgres available.

3. Expose a shared ``wipe_lea`` helper that tests use in fixture teardown
   so the demo runner does not see leftover ``lea-*`` rows. Cleanup is
   ordered by FK dependency: audit children first, then snapshots, then
   canonical, then operational tables, then the LEA itself.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text


# Modernization status, 2026-05-21: every entry point we own
# (alembic env, demo runner, seed module, API launcher) uses the
# Python 3.12+ ``asyncio.run(..., loop_factory=asyncio.SelectorEventLoop)``
# path. The deprecated ``set_event_loop_policy`` lives in exactly two
# places, both pytest-driven:
#
# 1. The ``event_loop_policy`` fixture below (the API pytest-asyncio
#    1.x exposes; the project has not yet adopted loop_factory).
# 2. The module-level install below, which applies to sync tests that
#    use Starlette's ``TestClient``. ``TestClient`` runs handlers via
#    anyio in its own loop; the fixture only applies inside async
#    tests, so sync tests need the process-global install.
#
# Both deprecation warnings are filtered in pyproject.toml so test
# output stays clean. Remove both once pytest-asyncio ships
# loop_factory support.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """Drop the lru-cached Settings before each test.

    Tests that monkeypatch ``EDLINK_PROFILE`` or other env vars need
    the next call to ``get_settings()`` to read the mutated environment
    rather than the stale cached instance from a prior test. Cheap
    (env parsing only) and worth the determinism.
    """

    from edlink_rostering.core.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture
def edlink_fixtures_dir() -> Path:
    """Path to fixtures/edlink/ relative to the prototype directory."""

    return Path(__file__).parent.parent / "fixtures" / "edlink"


@pytest_asyncio.fixture
async def db_session_factory() -> AsyncIterator[object]:
    """An async_sessionmaker bound to APP_DATABASE_URL.

    Skips dependent tests if APP_DATABASE_URL is unset, so the test suite
    still runs in environments without Postgres.
    """

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    url = os.environ.get("APP_DATABASE_URL")
    if not url:
        pytest.skip("APP_DATABASE_URL not set; skipping DB-bound test")

    engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


async def wipe_lea(session: Any, lea_id: str) -> None:
    """Delete every row that touches one LEA.

    Ordered by FK dependency: audit children of sync_jobs first, then
    snapshots, then canonical, then operational, then the LEA row.
    Tests call this from fixture teardown so the demo runner does not
    inherit leftover ``lea-*`` rows.
    """

    for table in (
        "sync_validation_results",
        "revert_actions",
        "retry_actions",
        "quarantine",
    ):
        await session.execute(
            text(
                f"DELETE FROM {table} WHERE sync_job_id IN "
                f"(SELECT id FROM sync_jobs WHERE lea_id = :l)"
            ),
            {"l": lea_id},
        )
    for table in (
        "student_snapshots",
        "enrollment_snapshots",
        "lea_snapshots",
    ):
        await session.execute(
            text(f"DELETE FROM {table} WHERE lea_id = :l"),
            {"l": lea_id},
        )
    # V0004 + V0005 + V0006 tables that reference leas(id). Delete
    # before the LEA so the FK constraints don't refuse the leas
    # delete.
    for table, col in (
        ("operator_lea_grant", "lea_id"),
        ("audit_log", "lea_id"),
        ("connector_authorization", "lea_id"),
        ("reconciliation_runs", "lea_id"),
    ):
        await session.execute(
            text(f"DELETE FROM {table} WHERE {col} = :l"),
            {"l": lea_id},
        )
    for table, col in (
        ("enrollments", "lea_id"),
        ("students", "lea_id"),
        ("classes", "lea_id"),
        ("academic_sessions", "lea_id"),
        ("schools", "lea_id"),
        ("sync_jobs", "lea_id"),
        ("cursor_state", "lea_id"),
        ("leas", "id"),
    ):
        await session.execute(
            text(f"DELETE FROM {table} WHERE {col} = :l"),
            {"l": lea_id},
        )


async def wipe_seeded_operators(session: Any) -> None:
    """Delete the seeded operator rows and their role grants.

    Used in tests that seed the dev personas so the operator/role
    tables come back clean. FK children are cleared in order:
    connector_authorization rows authored or revoked by the seeded
    operators (since the dev seed creates one per LEA),
    operator_role rows where the seeded operator was granted or
    revoked by another seeded operator, audit_log rows, then the
    operator rows themselves.
    """

    seeded_subjects = (
        "stephen-dev-001",
        "admin-dev-001",
        "qa-dev-001",
        "lakewood-ops-001",
        "district-ops-001",
        "auditor-001",
    )
    params = {"subj": list(seeded_subjects)}
    op_filter = (
        "id IN (SELECT id FROM operator WHERE subject = ANY(:subj))"
    )

    # operator_lea_grant references operator via operator_id and
    # granted_by/revoked_by. Clear before the operator delete.
    await session.execute(
        text(
            "DELETE FROM operator_lea_grant WHERE operator_id IN"
            " (SELECT id FROM operator WHERE subject = ANY(:subj))"
            " OR granted_by IN"
            " (SELECT id FROM operator WHERE subject = ANY(:subj))"
            " OR revoked_by IN"
            " (SELECT id FROM operator WHERE subject = ANY(:subj))"
        ),
        params,
    )

    # connector_authorization references operator via authorized_by /
    # revoked_by. The seed inserts one row per LEA authored by
    # stephen-dev-001; clear them before the operator delete.
    await session.execute(
        text(
            "DELETE FROM connector_authorization WHERE authorized_by"
            f" IN (SELECT id FROM operator WHERE subject = ANY(:subj))"
            " OR revoked_by IN"
            " (SELECT id FROM operator WHERE subject = ANY(:subj))"
        ),
        params,
    )
    await session.execute(
        text(
            "DELETE FROM audit_log WHERE operator_id IN"
            " (SELECT id FROM operator WHERE subject = ANY(:subj))"
        ),
        params,
    )
    # operator_role rows reference operator via operator_id AND
    # granted_by/revoked_by; the inter-operator references must clear
    # before the row delete.
    await session.execute(
        text(
            "DELETE FROM operator_role WHERE operator_id IN"
            " (SELECT id FROM operator WHERE subject = ANY(:subj))"
            " OR granted_by IN"
            " (SELECT id FROM operator WHERE subject = ANY(:subj))"
            " OR revoked_by IN"
            " (SELECT id FROM operator WHERE subject = ANY(:subj))"
        ),
        params,
    )
    await session.execute(
        text(f"DELETE FROM operator WHERE {op_filter}"),
        params,
    )
