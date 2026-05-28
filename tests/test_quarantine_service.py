"""QuarantineService tests.

Covers: list_unresolved scoping, release resolves once the student
arrives, release refuses when the FK is still missing, reject marks
the row resolved without canonical change, second action on a resolved
row raises QuarantineAlreadyResolved.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.core.types import LeaId
from edlink_rostering.services.quarantine import (
    QuarantineAlreadyResolved,
    QuarantineRefused,
    QuarantineService,
)
from tests.conftest import wipe_lea


@pytest_asyncio.fixture
async def lea_with_orphan(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """A quarantine row referencing a missing student.

    Returns (lea_id, sync_job_id, quarantine_id, enrollment_id,
    pending_student_id). Both IDs are unique per fixture invocation so
    tests can run in any order without primary-key collisions.
    """

    suffix = uuid.uuid4().hex[:8]
    lea_id = LeaId(f"lea-quar-{suffix}")
    sync_job_id = uuid.uuid4()
    quarantine_id = uuid.uuid4()
    enrollment_id = f"enr-quar-{suffix}"
    pending_student_id = f"stu-pending-{suffix}"
    now = datetime.now(UTC)
    payload = {
        "id": enrollment_id,
        "lea_id": lea_id,
        "student_id": pending_student_id,
        "class_id": "cls-X",
        "begin_date": "2026-08-15",
        "end_date": None,
        "source_event_id": f"evt_{suffix}",
    }
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'q', 'traditional_district', 'XX')
                """
            ),
            {"id": lea_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs (
                    id, lea_id, partner, status, started_at, completed_at,
                    event_count
                ) VALUES (
                    :id, :lea, 'edlink', 'success', :now, :now, 1
                )
                """
            ),
            {"id": sync_job_id, "lea": lea_id, "now": now},
        )
        await session.execute(
            text(
                """
                INSERT INTO quarantine (
                    id, sync_job_id, lea_id, entity_type, entity_id,
                    reason, raw_payload, created_at
                ) VALUES (
                    :id, :sj, :lea, 'enrollment', :eid,
                    'Layer 4: orphan', CAST(:payload AS JSONB), :now
                )
                """
            ),
            {
                "id": quarantine_id,
                "sj": sync_job_id,
                "lea": lea_id,
                "eid": payload["id"],
                "payload": json.dumps(payload),
                "now": now,
            },
        )
        await session.commit()

    yield lea_id, sync_job_id, quarantine_id, enrollment_id, pending_student_id

    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()


@pytest.mark.asyncio
async def test_list_unresolved_filters_by_lea(
    db_session_factory: async_sessionmaker[Any],
    lea_with_orphan: tuple[LeaId, uuid.UUID, uuid.UUID, str, str],
) -> None:
    lea_id, _, _, _, _ = lea_with_orphan
    service = QuarantineService(session_factory=db_session_factory)
    rows = await service.list_unresolved(lea_id=lea_id)
    assert len(rows) == 1
    assert rows[0].lea_id == lea_id
    assert rows[0].entity_type == "enrollment"


@pytest.mark.asyncio
async def test_release_refuses_when_target_student_missing(
    db_session_factory: async_sessionmaker[Any],
    lea_with_orphan: tuple[LeaId, uuid.UUID, uuid.UUID, str, str],
) -> None:
    _, _, quarantine_id, _, _ = lea_with_orphan
    service = QuarantineService(session_factory=db_session_factory)
    with pytest.raises(QuarantineRefused):
        await service.release(
            quarantine_id=quarantine_id,
            operator_identity="qa@edlink.test",
        )


@pytest.mark.asyncio
async def test_release_succeeds_once_student_arrives(
    db_session_factory: async_sessionmaker[Any],
    lea_with_orphan: tuple[LeaId, uuid.UUID, uuid.UUID, str, str],
) -> None:
    lea_id, _, quarantine_id, enrollment_id, pending_student_id = lea_with_orphan
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO students (
                    id, lea_id, given_name, family_name, grade,
                    external_ids
                ) VALUES (
                    :sid, :lea, 'P', 'Ending', '07',
                    CAST('{}' AS JSONB)
                )
                """
            ),
            {"sid": pending_student_id, "lea": lea_id},
        )
        await session.commit()

    service = QuarantineService(session_factory=db_session_factory)
    outcome = await service.release(
        quarantine_id=quarantine_id,
        operator_identity="qa@edlink.test",
    )
    assert outcome.entity_type == "enrollment"
    assert outcome.entity_id == enrollment_id

    async with db_session_factory() as session:
        enrollment = (
            await session.execute(
                text(
                    "SELECT student_id FROM enrollments WHERE id = :eid"
                ),
                {"eid": enrollment_id},
            )
        ).one()
        assert enrollment.student_id == pending_student_id

        snapshot = (
            await session.execute(
                text(
                    """
                    SELECT generation_id FROM enrollment_snapshots
                    WHERE enrollment_id = :eid
                    """
                ),
                {"eid": enrollment_id},
            )
        ).one()
        assert snapshot.generation_id == outcome.release_generation_id

        quarantine_row = (
            await session.execute(
                text(
                    "SELECT resolution_status FROM quarantine WHERE id = :id"
                ),
                {"id": quarantine_id},
            )
        ).one()
        assert quarantine_row.resolution_status == "released"


@pytest.mark.asyncio
async def test_reject_marks_resolved_without_canonical_change(
    db_session_factory: async_sessionmaker[Any],
    lea_with_orphan: tuple[LeaId, uuid.UUID, uuid.UUID, str, str],
) -> None:
    lea_id, _, quarantine_id, _, _ = lea_with_orphan
    service = QuarantineService(session_factory=db_session_factory)
    await service.reject(
        quarantine_id=quarantine_id,
        operator_identity="qa@edlink.test",
        reason="upstream confirmed bogus enrollment",
    )
    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT resolution_status, resolution_operator, reason
                    FROM quarantine WHERE id = :id
                    """
                ),
                {"id": quarantine_id},
            )
        ).one()
        assert row.resolution_status == "rejected"
        assert row.resolution_operator == "qa@edlink.test"
        assert "rejected:" in row.reason

        enrollment_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) AS n FROM enrollments WHERE lea_id = :lea"
                ),
                {"lea": lea_id},
            )
        ).one().n
        assert enrollment_count == 0


@pytest.mark.asyncio
async def test_second_action_on_resolved_row_raises(
    db_session_factory: async_sessionmaker[Any],
    lea_with_orphan: tuple[LeaId, uuid.UUID, uuid.UUID, str, str],
) -> None:
    _, _, quarantine_id, _, _ = lea_with_orphan
    service = QuarantineService(session_factory=db_session_factory)
    await service.reject(
        quarantine_id=quarantine_id,
        operator_identity="qa@edlink.test",
        reason="first rejection",
    )
    with pytest.raises(QuarantineAlreadyResolved):
        await service.reject(
            quarantine_id=quarantine_id,
            operator_identity="qa@edlink.test",
            reason="second rejection",
        )


# Keep date import in use; QuarantineService uses date.fromisoformat indirectly.
_ = date
