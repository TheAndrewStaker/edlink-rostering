"""Reconciliation scheduler tests.

Covers the daily sweep wrapper around
:class:`edlink_rostering.services.reconciliation.ReconciliationService`:

- Sweep walks every ``connector_authorization`` row with
  ``status='active'`` and reconciles each (lea_id, partner) pair.
- Per-LEA failures are recorded but do not abort the sweep.
- ``reconcile_one(force=True)`` bypasses the quiet-window check.
- Pending / revoked / locked authorizations are skipped.

The snapshot_provider is injected per the production seam, so the
test wires a synthetic callable that mirrors what
``EdLinkConnector.walk_resources`` would produce.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.core.types import LeaId
from edlink_rostering.services.reconciliation import ReconciliationService
from edlink_rostering.services.reconciliation_scheduler import (
    ReconciliationScheduler,
)
from tests.conftest import wipe_lea


pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("APP_DATABASE_URL")
        or os.environ.get("OPS_DATABASE_URL")
    ),
    reason="DB-bound; skipping",
)


_PARTNER = "edlink"


@pytest_asyncio.fixture
async def seeded_world(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Three LEAs: one matched, one drift-bound, one with a pending authz.

    The pending-authz LEA is here so the sweep can show it gets
    skipped. The two active LEAs both have stale-enough cursors that
    the quiet-window check passes.
    """

    matched_lea = LeaId(f"lea-sched-match-{uuid.uuid4().hex[:8]}")
    drift_lea = LeaId(f"lea-sched-drift-{uuid.uuid4().hex[:8]}")
    pending_lea = LeaId(f"lea-sched-pending-{uuid.uuid4().hex[:8]}")
    op_id = uuid.uuid4()

    async with db_session_factory() as session:
        for lea in (matched_lea, drift_lea, pending_lea):
            await session.execute(
                text(
                    """
                    INSERT INTO leas (id, name, lea_type, state)
                    VALUES (:id, :n, 'traditional_district', 'CA')
                    """
                ),
                {"id": lea, "n": f"sched {lea}"},
            )
        # An operator row to satisfy authorized_by FK.
        await session.execute(
            text(
                """
                INSERT INTO operator (id, subject, display_name, email, status)
                VALUES (:id, :s, 'Sched Test', 'sched@test', 'active')
                """
            ),
            {"id": op_id, "s": f"sched-test-{uuid.uuid4().hex[:8]}"},
        )
        # Matched LEA: one student, partner-side will mirror.
        await session.execute(
            text(
                """
                INSERT INTO students (
                    id, lea_id, given_name, family_name, grade, external_ids
                ) VALUES (
                    :id, :lea, 'Match', 'One', '04', CAST('{}' AS JSONB)
                )
                """
            ),
            {"id": "stu-match", "lea": matched_lea},
        )
        # Drift LEA: one student, partner-side will diverge.
        await session.execute(
            text(
                """
                INSERT INTO students (
                    id, lea_id, given_name, family_name, grade, external_ids
                ) VALUES (
                    :id, :lea, 'Drift', 'One', '04', CAST('{}' AS JSONB)
                )
                """
            ),
            {"id": "stu-drift", "lea": drift_lea},
        )
        # Cursors aged 2h so quiet-window passes.
        for lea in (matched_lea, drift_lea):
            await session.execute(
                text(
                    """
                    INSERT INTO cursor_state (
                        lea_id, partner, last_event_id, last_event_at,
                        last_poll_at, cold_start_required, updated_at
                    ) VALUES (
                        :lea, :p, 'evt_seed',
                        NOW() - INTERVAL '2 hours',
                        NOW() - INTERVAL '2 hours',
                        false, NOW()
                    )
                    """
                ),
                {"lea": lea, "p": _PARTNER},
            )
        # Authorizations: active for matched + drift, pending for the
        # third LEA so the sweep should not touch it.
        for lea, status in (
            (matched_lea, "active"),
            (drift_lea, "active"),
            (pending_lea, "pending"),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO connector_authorization (
                        lea_id, partner, status,
                        authorized_by, authorized_at
                    ) VALUES (
                        :lea, :p, :status,
                        :op, NOW()
                    )
                    """
                ),
                {
                    "lea": lea,
                    "p": _PARTNER,
                    "status": status,
                    "op": op_id,
                },
            )
        await session.commit()

    yield {
        "matched_lea": matched_lea,
        "drift_lea": drift_lea,
        "pending_lea": pending_lea,
        "operator_id": op_id,
    }

    async with db_session_factory() as session:
        for lea in (matched_lea, drift_lea, pending_lea):
            await wipe_lea(session, lea)
        await session.execute(
            text("DELETE FROM operator WHERE id = :id"),
            {"id": op_id},
        )
        await session.commit()


def _make_snapshot_provider(
    matched_lea: LeaId, drift_lea: LeaId
) -> Any:
    """Provider that mirrors canonical for one LEA and diverges for the other.

    Mimics the shape ``EdLinkConnector.walk_resources`` produces: a
    dict with ``students`` and ``enrollments`` lists.
    """

    async def provider(
        partner: str, lea_id: LeaId
    ) -> dict[str, list[dict[str, Any]]]:
        assert partner == _PARTNER
        if lea_id == matched_lea:
            return {
                "students": [
                    {
                        "id": "stu-match",
                        "given_name": "Match",
                        "family_name": "One",
                        "grade": "04",
                        "preferred_first_name": None,
                        "primary_school_id": None,
                    }
                ],
                "enrollments": [],
            }
        if lea_id == drift_lea:
            return {
                "students": [
                    {
                        "id": "stu-drift-OTHER",
                        "given_name": "Drift",
                        "family_name": "Two",
                        "grade": "04",
                        "preferred_first_name": None,
                        "primary_school_id": None,
                    }
                ],
                "enrollments": [],
            }
        raise AssertionError(f"unexpected lea_id {lea_id}")

    return provider


@pytest.mark.asyncio
async def test_daily_sweep_reconciles_active_authorizations(
    db_session_factory: async_sessionmaker[Any],
    seeded_world: dict[str, Any],
) -> None:
    matched_lea = seeded_world["matched_lea"]
    drift_lea = seeded_world["drift_lea"]
    pending_lea = seeded_world["pending_lea"]
    service = ReconciliationService(session_factory=db_session_factory)
    scheduler = ReconciliationScheduler(
        session_factory=db_session_factory,
        reconciliation_service=service,
        snapshot_provider=_make_snapshot_provider(matched_lea, drift_lea),
    )

    report = await scheduler.run_daily_sweep()

    assert report.total_authorizations == 2  # pending not counted
    assert report.matched_count == 1
    assert report.drift_count == 1
    assert report.skipped_count == 0
    assert report.failed_count == 0
    assert report.failures == []

    by_lea = {r.lea_id: r for r in report.per_lea}
    assert by_lea[matched_lea].status == "matched"
    assert by_lea[drift_lea].status == "drift_detected"
    assert pending_lea not in by_lea

    # Both runs landed in reconciliation_runs as audit history.
    async with db_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT lea_id, status
                    FROM reconciliation_runs
                    WHERE lea_id IN (:m, :d, :p)
                    """
                ),
                {"m": matched_lea, "d": drift_lea, "p": pending_lea},
            )
        ).all()
    states = {(r.lea_id, r.status) for r in rows}
    assert (matched_lea, "matched") in states
    assert (drift_lea, "drift_detected") in states
    assert not any(lea == pending_lea for lea, _ in states)


@pytest.mark.asyncio
async def test_sweep_continues_on_per_lea_failure(
    db_session_factory: async_sessionmaker[Any],
    seeded_world: dict[str, Any],
) -> None:
    """One snapshot_provider exception is recorded; the other LEA still runs."""

    matched_lea = seeded_world["matched_lea"]
    drift_lea = seeded_world["drift_lea"]

    async def flaky_provider(
        partner: str, lea_id: LeaId
    ) -> dict[str, list[dict[str, Any]]]:
        if lea_id == drift_lea:
            raise RuntimeError("simulated partner outage")
        return {
            "students": [
                {
                    "id": "stu-match",
                    "given_name": "Match",
                    "family_name": "One",
                    "grade": "04",
                    "preferred_first_name": None,
                    "primary_school_id": None,
                }
            ],
            "enrollments": [],
        }

    service = ReconciliationService(session_factory=db_session_factory)
    scheduler = ReconciliationScheduler(
        session_factory=db_session_factory,
        reconciliation_service=service,
        snapshot_provider=flaky_provider,
    )

    report = await scheduler.run_daily_sweep()

    assert report.matched_count == 1
    assert report.failed_count == 1
    assert len(report.failures) == 1
    failed_lea, failed_partner, msg = report.failures[0]
    assert failed_lea == drift_lea
    assert failed_partner == _PARTNER
    assert "simulated partner outage" in msg


@pytest.mark.asyncio
async def test_reconcile_one_force_bypasses_quiet_window(
    db_session_factory: async_sessionmaker[Any],
    seeded_world: dict[str, Any],
) -> None:
    """Recent cursor activity + force=True still reconciles."""

    matched_lea = seeded_world["matched_lea"]

    # Bump cursor to 5 minutes ago so the default quiet window rejects.
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                UPDATE cursor_state
                SET last_event_at = NOW() - INTERVAL '5 minutes'
                WHERE lea_id = :lea AND partner = :p
                """
            ),
            {"lea": matched_lea, "p": _PARTNER},
        )
        await session.commit()

    service = ReconciliationService(session_factory=db_session_factory)
    scheduler = ReconciliationScheduler(
        session_factory=db_session_factory,
        reconciliation_service=service,
        snapshot_provider=_make_snapshot_provider(
            matched_lea, LeaId("lea-unused")
        ),
    )

    # Without force: skipped.
    skipped = await scheduler.reconcile_one(
        lea_id=matched_lea, partner=_PARTNER
    )
    assert skipped.status == "skipped_quiet_window"

    # With force: actually reconciles.
    forced = await scheduler.reconcile_one(
        lea_id=matched_lea, partner=_PARTNER, force=True
    )
    assert forced.status == "matched"


@pytest.mark.asyncio
async def test_sweep_with_no_active_authorizations_returns_empty_report(
    db_session_factory: async_sessionmaker[Any],
) -> None:
    """Empty connector_authorization table → SweepReport with zero counts.

    Production may run the sweep before any LEA is onboarded; that case
    should complete cleanly rather than fail.
    """

    async def unused_provider(
        partner: str, lea_id: LeaId
    ) -> dict[str, list[dict[str, Any]]]:
        raise AssertionError(
            "snapshot_provider should not be called when no active authzs"
        )

    service = ReconciliationService(session_factory=db_session_factory)
    scheduler = ReconciliationScheduler(
        session_factory=db_session_factory,
        reconciliation_service=service,
        snapshot_provider=unused_provider,
    )

    # Filter to a synthetic partner with no rows in the DB by running
    # against a fresh transactional snapshot: the seeded operators may
    # have inserted some authzs in conftest, so we count only what was
    # walked rather than asserting the absolute total.
    async with db_session_factory() as session:
        active_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) AS n FROM connector_authorization"
                    " WHERE status = 'active'"
                )
            )
        ).scalar_one()

    if active_count > 0:
        pytest.skip(
            "DB has pre-existing active connector_authorization rows;"
            " this test asserts the empty-table path only."
        )

    report = await scheduler.run_daily_sweep()
    assert report.total_authorizations == 0
    assert report.per_lea == []
    assert report.failures == []
