"""Bulk-load cold-start path tests.

The bulk-load is what unwedges an LEA whose cursor fell past the
30-day Events API retention ceiling. Tests cover:

- Happy path: fresh LEA, bulk-load creates canonical + snapshots
  + sync_jobs row + clears cold_start_required.
- Idempotency: a second bulk-load against the same partner state
  writes zero new snapshot rows.
- Drift: a second bulk-load against changed partner state writes new
  snapshots only for the changed rows.
- Cold-start flag clear: bulk-load runs against an LEA whose
  ``cold_start_required = true`` and the flag is cleared at the end.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.core.types import Cursor, LeaId
from edlink_rostering.services.bulk_load import BulkLoadService
from tests.conftest import wipe_lea


pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("APP_DATABASE_URL")
        or os.environ.get("OPS_DATABASE_URL")
    ),
    reason="DB-bound; skipping",
)


_TEST_LEA = LeaId("lea-bulk-test")
_PARTNER = "edlink"


def _fixture_snapshot() -> dict[str, list[dict[str, Any]]]:
    return {
        "students": [
            {
                "id": "stu-bulk-001",
                "given_name": "Alex",
                "family_name": "Morgan",
                "grade": "05",
                "preferred_first_name": None,
                "primary_school_id": None,
            },
            {
                "id": "stu-bulk-002",
                "given_name": "Bryn",
                "family_name": "Lee",
                "grade": "06",
                "preferred_first_name": None,
                "primary_school_id": None,
            },
        ],
        "enrollments": [
            {
                "id": "enr-bulk-001",
                "student_id": "stu-bulk-001",
                "class_id": "cls-bulk-5a",
                "begin_date": "2026-08-15",
                "end_date": None,
            }
        ],
    }


def _fixture_partner_snapshot():
    async def snapshot(
        lea_id: LeaId,
    ) -> dict[str, list[dict[str, Any]]]:
        return _fixture_snapshot()

    return snapshot


def _fixture_latest_cursor():
    async def provider(lea_id: LeaId) -> Cursor:
        from datetime import UTC, datetime

        return Cursor(
            value="evt_bulk_last", observed_at=datetime.now(UTC)
        )

    return provider


@pytest_asyncio.fixture
async def clean_lea(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Pre- and post-test wipe of the bulk-load test LEA."""

    async with db_session_factory() as session:
        await wipe_lea(session, _TEST_LEA)
        await session.commit()
    yield
    async with db_session_factory() as session:
        await wipe_lea(session, _TEST_LEA)
        await session.commit()


@pytest.mark.asyncio
async def test_bulk_load_happy_path(
    db_session_factory: async_sessionmaker[Any],
    clean_lea: Any,
) -> None:
    service = BulkLoadService(session_factory=db_session_factory)
    report = await service.bulk_load_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=_fixture_partner_snapshot(),
        latest_cursor_provider=_fixture_latest_cursor(),
    )

    assert report.status == "success"
    assert report.rows_per_entity_type == {
        "students": 2,
        "enrollments": 1,
    }
    assert report.snapshots_written == {
        "students": 2,
        "enrollments": 1,
    }
    assert report.cold_start_cleared is True
    assert report.cursor_after == "evt_bulk_last"

    async with db_session_factory() as session:
        student_rows = (
            await session.execute(
                text(
                    "SELECT id, given_name FROM students WHERE lea_id = :l"
                ),
                {"l": _TEST_LEA},
            )
        ).all()
        snapshot_rows = (
            await session.execute(
                text(
                    """
                    SELECT student_id FROM student_snapshots
                    WHERE lea_id = :l
                      AND superseded_by_generation_id IS NULL
                    """
                ),
                {"l": _TEST_LEA},
            )
        ).all()
        cursor_row = (
            await session.execute(
                text(
                    """
                    SELECT last_event_id, cold_start_required
                    FROM cursor_state
                    WHERE lea_id = :l AND partner = :p
                    """
                ),
                {"l": _TEST_LEA, "p": _PARTNER},
            )
        ).one()
        sync_job_row = (
            await session.execute(
                text(
                    """
                    SELECT status, event_count
                    FROM sync_jobs WHERE id = :id
                    """
                ),
                {"id": report.sync_job_id},
            )
        ).one()

    assert len(student_rows) == 2
    assert len(snapshot_rows) == 2
    assert cursor_row.last_event_id == "evt_bulk_last"
    assert cursor_row.cold_start_required is False
    assert sync_job_row.status == "success"
    assert sync_job_row.event_count == 3


@pytest.mark.asyncio
async def test_bulk_load_idempotent_no_change(
    db_session_factory: async_sessionmaker[Any],
    clean_lea: Any,
) -> None:
    """Re-running bulk-load against unchanged partner state writes zero new snapshots."""

    service = BulkLoadService(session_factory=db_session_factory)
    await service.bulk_load_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=_fixture_partner_snapshot(),
        latest_cursor_provider=_fixture_latest_cursor(),
    )
    second_report = await service.bulk_load_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=_fixture_partner_snapshot(),
        latest_cursor_provider=_fixture_latest_cursor(),
    )
    assert second_report.snapshots_written == {
        "students": 0,
        "enrollments": 0,
    }


@pytest.mark.asyncio
async def test_bulk_load_writes_only_changed_rows_on_drift(
    db_session_factory: async_sessionmaker[Any],
    clean_lea: Any,
) -> None:
    """A second bulk-load with one changed student writes 1 student snapshot."""

    service = BulkLoadService(session_factory=db_session_factory)
    await service.bulk_load_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=_fixture_partner_snapshot(),
        latest_cursor_provider=_fixture_latest_cursor(),
    )

    async def drifted_snapshot(
        lea_id: LeaId,
    ) -> dict[str, list[dict[str, Any]]]:
        payload = _fixture_snapshot()
        # Alex graduated to grade 06 between bulk-loads.
        payload["students"][0]["grade"] = "06"
        return payload

    second_report = await service.bulk_load_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=drifted_snapshot,
        latest_cursor_provider=_fixture_latest_cursor(),
    )
    assert second_report.snapshots_written == {
        "students": 1,
        "enrollments": 0,
    }

    async with db_session_factory() as session:
        alex = (
            await session.execute(
                text(
                    "SELECT grade FROM students WHERE id = 'stu-bulk-001'"
                ),
            )
        ).one()
    assert alex.grade == "06"


@pytest.mark.asyncio
async def test_bulk_load_clears_cold_start_required_flag(
    db_session_factory: async_sessionmaker[Any],
    clean_lea: Any,
) -> None:
    """Bulk-load run against an LEA with cold_start_required=true clears the flag."""

    # Seed an LEA + cursor with cold_start_required=true, simulating
    # the poll worker's "cursor exceeded retention" detection.
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'Cold Start LEA', 'traditional_district', 'CA')
                """
            ),
            {"id": _TEST_LEA},
        )
        await session.execute(
            text(
                """
                INSERT INTO cursor_state (
                    lea_id, partner, last_event_id, last_event_at,
                    last_poll_at, cold_start_required, updated_at
                ) VALUES (
                    :lea, :partner, 'evt_stale', NOW() - INTERVAL '40 days',
                    NOW(), true, NOW()
                )
                """
            ),
            {"lea": _TEST_LEA, "partner": _PARTNER},
        )
        await session.commit()

    service = BulkLoadService(session_factory=db_session_factory)
    await service.bulk_load_lea(
        lea_id=_TEST_LEA,
        partner=_PARTNER,
        partner_snapshot=_fixture_partner_snapshot(),
        latest_cursor_provider=_fixture_latest_cursor(),
    )

    async with db_session_factory() as session:
        cursor_row = (
            await session.execute(
                text(
                    """
                    SELECT last_event_id, cold_start_required
                    FROM cursor_state
                    WHERE lea_id = :l AND partner = :p
                    """
                ),
                {"l": _TEST_LEA, "p": _PARTNER},
            )
        ).one()
    assert cursor_row.cold_start_required is False
    assert cursor_row.last_event_id == "evt_bulk_last"
