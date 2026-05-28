"""Tests for the dev seed module.

The seed has two contracts:

1. **Idempotent.** Running it twice produces the same state as running
   it once. No duplicate rows, no growing counts.
2. **Operational diversity.** After a single run, the five seeded LEAs
   land in the five expected states the admin app demos: happy with
   history, recently reverted, failed-sync, quarantine-heavy, stale
   cursor.

These tests use a per-test cleanup of the seeded LEAs so the demo
runner and other integration tests do not inherit seed state.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.dev.seed import (
    SEEDED_LEAS,
    SEEDED_OPERATORS,
    seed_realistic_state,
)
from tests.conftest import wipe_lea, wipe_seeded_operators


@pytest_asyncio.fixture
async def cleanup_seeded(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Wipe every seeded LEA and operator after the test runs."""

    yield None

    async with db_session_factory() as session:
        for lea in SEEDED_LEAS:
            await wipe_lea(session, lea.id)
        await wipe_seeded_operators(session)
        await session.commit()


@pytest.mark.asyncio
async def test_seed_inserts_five_leas(
    db_session_factory: async_sessionmaker[Any],
    cleanup_seeded: Any,
) -> None:
    await seed_realistic_state(db_session_factory)
    async with db_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id FROM leas
                    WHERE id IN ('lea-lakewood-usd', 'lea-northridge-sd',
                                 'lea-valley-charter', 'lea-hillcrest-usd',
                                 'lea-riverside-usd')
                    """
                )
            )
        ).all()
    assert len(rows) == 5


@pytest.mark.asyncio
async def test_seed_is_idempotent(
    db_session_factory: async_sessionmaker[Any],
    cleanup_seeded: Any,
) -> None:
    await seed_realistic_state(db_session_factory)
    await seed_realistic_state(db_session_factory)

    async with db_session_factory() as session:
        lea_count = _scalar(
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM leas
                    WHERE id LIKE 'lea-lakewood-usd' OR id LIKE 'lea-northridge-sd'
                       OR id LIKE 'lea-valley-charter' OR id LIKE 'lea-hillcrest-usd'
                       OR id LIKE 'lea-riverside-usd'
                    """
                )
            )
        )
        sync_count = _scalar(
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM sync_jobs
                    WHERE lea_id IN ('lea-lakewood-usd', 'lea-northridge-sd',
                                     'lea-valley-charter', 'lea-hillcrest-usd',
                                     'lea-riverside-usd')
                    """
                )
            )
        )
        quarantine_count = _scalar(
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM quarantine
                    WHERE lea_id = 'lea-hillcrest-usd'
                    """
                )
            )
        )

    assert lea_count == 5
    # Per-LEA scenario syncs: Lakewood 5 + Northridge 6 + Valley 2 +
    # Hillcrest 1 + Riverside 1 = 15. The seed also writes
    # ``_seed_sync_activity_history`` rows (16 per LEA across four LEAs
    # = 64) so the sync-jobs roll-up under the activity chart looks
    # populated. Total = 79. The idempotency invariant is "running
    # the seed twice yields the same count as running it once," which
    # is what the assertion checks; the absolute number is a function
    # of the seed's contents.
    assert sync_count == 79
    assert quarantine_count == 30


@pytest.mark.asyncio
async def test_seed_produces_stale_cursor_for_riverside(
    db_session_factory: async_sessionmaker[Any],
    cleanup_seeded: Any,
) -> None:
    """Riverside's cursor lands at exactly 25 days behind so the
    cursor_lag_20_day alert fires every time the seed runs."""

    from datetime import UTC, datetime, timedelta

    await seed_realistic_state(db_session_factory)
    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT last_event_at FROM cursor_state
                    WHERE lea_id = 'lea-riverside-usd' AND partner = 'edlink'
                    """
                )
            )
        ).one()
    lag = (datetime.now(UTC) - row.last_event_at).total_seconds() / 86400.0
    # Cursor should be ~25 days behind, well past the 20-day threshold.
    assert lag >= 24.9
    assert lag <= 25.1


@pytest.mark.asyncio
async def test_seed_makes_valley_charter_sync_fail(
    db_session_factory: async_sessionmaker[Any],
    cleanup_seeded: Any,
) -> None:
    """Valley Charter's latest sync is 'failed' so the sync_failure
    alert can be demoed via the admin app's retry button."""

    await seed_realistic_state(db_session_factory)
    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT status, error_summary FROM sync_jobs
                    WHERE lea_id = 'lea-valley-charter'
                    ORDER BY started_at DESC LIMIT 1
                    """
                )
            )
        ).one()
    assert row.status == "failed"
    assert row.error_summary is not None
    assert "SCHEMA_MISSING_FIELD" in row.error_summary


@pytest.mark.asyncio
async def test_seed_inserts_operator_personas_with_role_grants(
    db_session_factory: async_sessionmaker[Any],
    cleanup_seeded: Any,
) -> None:
    """The six personas land with one active role each.

    The role grants are what Phase 1.5a's authz tests target; if the
    seed forgets to insert an `operator_role` row the auth module
    treats the persona as roleless and every action 403s.
    """

    await seed_realistic_state(db_session_factory)
    async with db_session_factory() as session:
        op_count = _scalar(
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM operator "
                    "WHERE subject = ANY(:subj)"
                ),
                {"subj": [op.subject for op in SEEDED_OPERATORS]},
            )
        )
        role_count = _scalar(
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM operator_role r "
                    "JOIN operator o ON o.id = r.operator_id "
                    "WHERE o.subject = ANY(:subj) AND r.revoked_at IS NULL"
                ),
                {"subj": [op.subject for op in SEEDED_OPERATORS]},
            )
        )
        founders = _scalar(
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM operator_role r "
                    "WHERE r.role = 'owner' AND r.revoked_at IS NULL"
                ),
            )
        )
    assert op_count == len(SEEDED_OPERATORS)
    assert role_count == len(SEEDED_OPERATORS)
    assert founders == 2  # stephen + admin


@pytest.mark.asyncio
async def test_seed_grants_single_lea_operators_their_lea(
    db_session_factory: async_sessionmaker[Any],
    cleanup_seeded: Any,
) -> None:
    """V0005 operator_lea_grant rows for the two single-LEA personas.

    lakewood-ops-001 is granted lea-lakewood-usd, and
    district-ops-001 is granted lea-riverside-usd. No other
    persona gets grants in the seed; owner / admin
    / auditor have implicit organization-wide access.
    """

    await seed_realistic_state(db_session_factory)
    async with db_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT o.subject, g.lea_id
                    FROM operator_lea_grant g
                    JOIN operator o ON o.id = g.operator_id
                    WHERE g.revoked_at IS NULL
                    ORDER BY o.subject
                    """
                ),
            )
        ).all()
    grants = {(r.subject, r.lea_id) for r in rows}
    assert grants == {
        ("lakewood-ops-001", "lea-lakewood-usd"),
        ("district-ops-001", "lea-riverside-usd"),
    }


@pytest.mark.asyncio
async def test_seed_inserts_connector_authorization_per_lea(
    db_session_factory: async_sessionmaker[Any],
    cleanup_seeded: Any,
) -> None:
    """One active edlink authorization per seeded LEA.

    The connector-management UI in Phase 1.5d reads from this table;
    the demo data needs an active row for each of the five LEAs so the
    list view is not blank.
    """

    await seed_realistic_state(db_session_factory)
    async with db_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT lea_id, status, secret_ref
                    FROM connector_authorization
                    WHERE partner = 'edlink'
                      AND lea_id = ANY(:leas)
                    ORDER BY lea_id
                    """
                ),
                {"leas": [lea.id for lea in SEEDED_LEAS]},
            )
        ).all()
    assert len(rows) == len(SEEDED_LEAS)
    assert all(r.status == "active" for r in rows)
    assert all(r.secret_ref.startswith("edlink-token-") for r in rows)


def _scalar(result: Any) -> Any:
    return result.one()[0]


# Make uuid import explicit so mypy + linters keep the dependency in mind
# even though tests use the helper directly.
_ = uuid
