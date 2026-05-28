"""Merkle reconciliation service tests.

Covers the three outcomes per the design doc § "Reconciliation":

- Matched: canonical and partner roots agree, run row records status
  ``matched`` and no drift summary.
- Drift detected: per-entity-type mid hashes diverge, run row carries
  status ``drift_detected`` plus a JSON drift summary listing
  canonical_only and partner_only entity ids on each side.
- Skipped quiet window: cursor's ``last_event_at`` is within the
  required quiet window, the service short-circuits and writes a
  ``skipped_quiet_window`` row without touching canonical state.

Tests seed canonical state directly so the focus stays on the
reconciliation logic, not on driving the sync worker. The partner-side
snapshot is supplied as an injected callable per the production seam
(the real walk over EdLink's resource endpoints lives elsewhere).
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
from edlink_rostering.services.reconciliation import (
    PartnerSnapshot,
    ReconciliationReport,
    ReconciliationService,
)
from tests.conftest import wipe_lea


pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("APP_DATABASE_URL")
        or os.environ.get("OPS_DATABASE_URL")
    ),
    reason="DB-bound; skipping",
)


_TEST_LEA = LeaId("lea-recon-test")
_PARTNER = "edlink"


@pytest_asyncio.fixture
async def seeded_canonical(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Seed two students + one enrollment + an old cursor."""

    async with db_session_factory() as session:
        await wipe_lea(session, _TEST_LEA)
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'Recon Test LEA', 'traditional_district', 'CA')
                """
            ),
            {"id": _TEST_LEA},
        )
        await session.execute(
            text(
                """
                INSERT INTO students (
                    id, lea_id, given_name, family_name, grade, external_ids
                ) VALUES
                    (:s1, :lea, 'Alex', 'Morgan', '05', CAST('{}' AS JSONB)),
                    (:s2, :lea, 'Bryn', 'Lee', '06', CAST('{}' AS JSONB))
                """
            ),
            {
                "lea": _TEST_LEA,
                "s1": "stu-recon-001",
                "s2": "stu-recon-002",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO enrollments (
                    id, lea_id, student_id, class_id, begin_date
                ) VALUES (
                    :id, :lea, :stu, 'cls-recon-5a', '2026-08-15'
                )
                """
            ),
            {
                "id": "enr-recon-001",
                "lea": _TEST_LEA,
                "stu": "stu-recon-001",
            },
        )
        # Cursor 2 hours old so the quiet-window check (default 60 min)
        # passes without forcing every test to reset it.
        await session.execute(
            text(
                """
                INSERT INTO cursor_state (
                    lea_id, partner, last_event_id, last_event_at,
                    last_poll_at, cold_start_required, updated_at
                ) VALUES (
                    :lea, :p, 'evt_recon_seed',
                    NOW() - INTERVAL '2 hours',
                    NOW() - INTERVAL '2 hours',
                    false, NOW()
                )
                """
            ),
            {"lea": _TEST_LEA, "p": _PARTNER},
        )
        await session.commit()

    yield

    async with db_session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM reconciliation_runs WHERE lea_id = :l"
            ),
            {"l": _TEST_LEA},
        )
        await wipe_lea(session, _TEST_LEA)
        await session.commit()


def _matching_partner_snapshot() -> PartnerSnapshot:
    """Partner-side snapshot that matches the canonical seed exactly."""

    async def snapshot(lea_id: LeaId) -> dict[str, list[dict[str, Any]]]:
        return {
            "students": [
                {
                    "id": "stu-recon-001",
                    "lea_id": lea_id,
                    "given_name": "Alex",
                    "family_name": "Morgan",
                    "grade": "05",
                    "preferred_first_name": None,
                    "primary_school_id": None,
                },
                {
                    "id": "stu-recon-002",
                    "lea_id": lea_id,
                    "given_name": "Bryn",
                    "family_name": "Lee",
                    "grade": "06",
                    "preferred_first_name": None,
                    "primary_school_id": None,
                },
            ],
            "enrollments": [
                {
                    "id": "enr-recon-001",
                    "lea_id": lea_id,
                    "student_id": "stu-recon-001",
                    "class_id": "cls-recon-5a",
                    "begin_date": "2026-08-15",
                    "end_date": None,
                }
            ],
        }

    return snapshot


@pytest.mark.asyncio
async def test_reconcile_matched(
    db_session_factory: async_sessionmaker[Any],
    seeded_canonical: Any,
) -> None:
    service = ReconciliationService(session_factory=db_session_factory)
    report = await service.reconcile_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=_matching_partner_snapshot(),
    )
    assert report.status == "matched"
    assert report.canonical_root_hash == report.partner_root_hash
    assert report.drift == ()

    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT status, canonical_root_hash, partner_root_hash,
                           drift_summary
                    FROM reconciliation_runs
                    WHERE id = :id
                    """
                ),
                {"id": report.id},
            )
        ).one()
    assert row.status == "matched"
    assert row.canonical_root_hash == row.partner_root_hash
    assert row.drift_summary is None


@pytest.mark.asyncio
async def test_reconcile_drift_extra_partner_row(
    db_session_factory: async_sessionmaker[Any],
    seeded_canonical: Any,
) -> None:
    """Partner has a student canonical doesn't; mid hash diverges."""

    async def snapshot(lea_id: LeaId) -> dict[str, list[dict[str, Any]]]:
        base = await _matching_partner_snapshot()(lea_id)
        base["students"].append(
            {
                "id": "stu-recon-extra",
                "lea_id": lea_id,
                "given_name": "Ghost",
                "family_name": "Student",
                "grade": "05",
                "preferred_first_name": None,
                "primary_school_id": None,
            }
        )
        return base

    service = ReconciliationService(session_factory=db_session_factory)
    report = await service.reconcile_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=snapshot,
    )
    assert report.status == "drift_detected"
    assert report.canonical_root_hash != report.partner_root_hash
    drift_by_type = {d.entity_type: d for d in report.drift}
    assert "students" in drift_by_type
    assert "enrollments" not in drift_by_type
    assert "stu-recon-extra" in drift_by_type["students"].partner_only_ids

    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT status, drift_summary
                    FROM reconciliation_runs
                    WHERE id = :id
                    """
                ),
                {"id": report.id},
            )
        ).one()
    assert row.status == "drift_detected"
    assert row.drift_summary is not None
    assert any(
        d["entity_type"] == "students" for d in row.drift_summary
    )


@pytest.mark.asyncio
async def test_reconcile_drift_field_change(
    db_session_factory: async_sessionmaker[Any],
    seeded_canonical: Any,
) -> None:
    """Same id sets but a field changed; mid hash diverges."""

    async def snapshot(lea_id: LeaId) -> dict[str, list[dict[str, Any]]]:
        base = await _matching_partner_snapshot()(lea_id)
        # Same students, but partner says Alex is grade 06.
        base["students"][0]["grade"] = "06"
        return base

    service = ReconciliationService(session_factory=db_session_factory)
    report = await service.reconcile_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=snapshot,
    )
    assert report.status == "drift_detected"
    drift_by_type = {d.entity_type: d for d in report.drift}
    assert "students" in drift_by_type
    # Both sides have the same id set; field change drives the mismatch.
    assert drift_by_type["students"].canonical_only_ids == ()
    assert drift_by_type["students"].partner_only_ids == ()
    assert (
        drift_by_type["students"].canonical_mid_hash
        != drift_by_type["students"].partner_mid_hash
    )


@pytest.mark.asyncio
async def test_reconcile_skipped_quiet_window(
    db_session_factory: async_sessionmaker[Any],
    seeded_canonical: Any,
) -> None:
    """Cursor too recent → service writes skipped_quiet_window row."""

    # Bump the cursor to 5 minutes ago so the 60-minute quiet window
    # rejects the run.
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                UPDATE cursor_state
                SET last_event_at = NOW() - INTERVAL '5 minutes'
                WHERE lea_id = :lea AND partner = :p
                """
            ),
            {"lea": _TEST_LEA, "p": _PARTNER},
        )
        await session.commit()

    service = ReconciliationService(session_factory=db_session_factory)
    snapshot_calls: list[LeaId] = []

    async def snapshot_should_not_be_called(
        lea_id: LeaId,
    ) -> dict[str, list[dict[str, Any]]]:
        snapshot_calls.append(lea_id)
        return {}

    report = await service.reconcile_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=snapshot_should_not_be_called,
    )
    assert report.status == "skipped_quiet_window"
    assert report.partner_root_hash is None
    assert snapshot_calls == []  # short-circuit before calling partner

    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT status FROM reconciliation_runs
                    WHERE id = :id
                    """
                ),
                {"id": report.id},
            )
        ).one()
    assert row.status == "skipped_quiet_window"


@pytest.mark.asyncio
async def test_reconcile_with_zero_quiet_minutes_skips_check(
    db_session_factory: async_sessionmaker[Any],
    seeded_canonical: Any,
) -> None:
    """``require_quiet_minutes=0`` bypasses the quiet-window guard.

    The forced-reconcile CLI path will use this to let the operator
    investigate outside the 02:00 LEA-local window.
    """

    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                UPDATE cursor_state
                SET last_event_at = NOW()
                WHERE lea_id = :lea AND partner = :p
                """
            ),
            {"lea": _TEST_LEA, "p": _PARTNER},
        )
        await session.commit()

    service = ReconciliationService(session_factory=db_session_factory)
    report = await service.reconcile_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=_matching_partner_snapshot(),
        require_quiet_minutes=0,
    )
    assert report.status == "matched"
