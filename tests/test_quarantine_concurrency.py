"""Concurrency guarantees on QuarantineService.release.

Two parallel release calls on the same quarantine row must produce
exactly one synthetic sync_jobs row of ``status='quarantine_release'``
and exactly one ``resolved_at`` value. The losing caller raises
``QuarantineAlreadyResolved``. Without ``SELECT ... FOR UPDATE`` on
the release path, two arrivals would each read the row as unresolved,
both insert their own synthetic sync_jobs row, and the audit log
would double-count the action.

This test pins the contract that a prior review called out.
See the matching Step 4 entry in ``docs/sprints/poc-session-4-plan.md``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.core.types import LeaId
from edlink_rostering.services.quarantine import (
    QuarantineAlreadyResolved,
    QuarantineService,
)
from tests.conftest import wipe_lea


@pytest_asyncio.fixture
async def releasable_orphan(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """A quarantine row whose target student now exists, ready to release.

    Yields (lea_id, quarantine_id). Cleans up the LEA on teardown.
    """

    suffix = uuid.uuid4().hex[:8]
    lea_id = LeaId(f"lea-conc-{suffix}")
    sync_job_id = uuid.uuid4()
    quarantine_id = uuid.uuid4()
    enrollment_id = f"enr-conc-{suffix}"
    student_id = f"stu-conc-{suffix}"
    now = datetime.now(UTC)
    payload = {
        "id": enrollment_id,
        "lea_id": lea_id,
        "student_id": student_id,
        "class_id": "cls-conc",
        "begin_date": "2026-08-15",
        "end_date": None,
        "source_event_id": f"evt_conc_{suffix}",
    }
    async with db_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO leas (id, name, lea_type, state) "
                "VALUES (:id, 'c', 'traditional_district', 'XX')"
            ),
            {"id": lea_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs
                    (id, lea_id, partner, status, started_at,
                     completed_at, event_count)
                VALUES (:id, :lea, 'edlink', 'success', :now, :now, 1)
                """
            ),
            {"id": sync_job_id, "lea": lea_id, "now": now},
        )
        await session.execute(
            text(
                """
                INSERT INTO students
                    (id, lea_id, given_name, family_name, grade,
                     external_ids)
                VALUES (:sid, :lea, 'C', 'Onc', '07', CAST('{}' AS JSONB))
                """
            ),
            {"sid": student_id, "lea": lea_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO quarantine
                    (id, sync_job_id, lea_id, entity_type, entity_id,
                     reason, raw_payload, created_at)
                VALUES (:id, :sj, :lea, 'enrollment', :eid,
                        'Layer 4: orphan', CAST(:payload AS JSONB), :now)
                """
            ),
            {
                "id": quarantine_id,
                "sj": sync_job_id,
                "lea": lea_id,
                "eid": enrollment_id,
                "payload": json.dumps(payload),
                "now": now,
            },
        )
        await session.commit()

    yield lea_id, quarantine_id

    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()


@pytest.mark.asyncio
async def test_two_parallel_release_calls_produce_one_synthetic_sync_job(
    db_session_factory: async_sessionmaker[Any],
    releasable_orphan: tuple[LeaId, uuid.UUID],
) -> None:
    """The losing caller raises; the audit log records exactly one action."""

    lea_id, quarantine_id = releasable_orphan
    service = QuarantineService(session_factory=db_session_factory)

    results = await asyncio.gather(
        service.release(
            quarantine_id=quarantine_id,
            operator_identity="conc-op-1@edlink.test",
        ),
        service.release(
            quarantine_id=quarantine_id,
            operator_identity="conc-op-2@edlink.test",
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1, (
        f"Exactly one release should succeed; got {len(successes)}: "
        f"{successes!r}"
    )
    assert len(failures) == 1, (
        f"Exactly one release should fail with already-resolved; got "
        f"{len(failures)}: {failures!r}"
    )
    assert isinstance(failures[0], QuarantineAlreadyResolved), (
        f"Loser should raise QuarantineAlreadyResolved, got "
        f"{type(failures[0]).__name__}: {failures[0]}"
    )

    async with db_session_factory() as session:
        synth_count = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM sync_jobs
                    WHERE lea_id = :lea
                      AND status = 'quarantine_release'
                    """
                ),
                {"lea": lea_id},
            )
        ).scalar()
        resolved_rows = (
            await session.execute(
                text(
                    """
                    SELECT resolved_at, resolution_status,
                           resolution_operator
                    FROM quarantine WHERE id = :id
                    """
                ),
                {"id": quarantine_id},
            )
        ).all()

    assert synth_count == 1, (
        f"Expected exactly one quarantine_release sync_jobs row; got "
        f"{synth_count}"
    )
    assert len(resolved_rows) == 1
    assert resolved_rows[0].resolved_at is not None
    assert resolved_rows[0].resolution_status == "released"
    # The operator who won the race is recorded in the audit row.
    assert resolved_rows[0].resolution_operator in (
        "conc-op-1@edlink.test",
        "conc-op-2@edlink.test",
    )
