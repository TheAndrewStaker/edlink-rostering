"""V0003 snapshot UPDATE trigger tests.

Confirms that the BEFORE UPDATE trigger raises on any column change
other than the two supersession columns, and that supersession updates
themselves still work (this is the path revert needs).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.core.types import LeaId
from tests.conftest import wipe_lea


async def _seed_lea_and_snapshot(
    session: Any, lea_id: LeaId
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Insert one student snapshot for the LEA.

    Returns (sync_job_id, snapshot_id, student_id). The student id is
    unique per LEA so the trigger tests can run in any order without
    primary-key collisions on the canonical ``students`` table.
    """

    sync_job_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    student_id = f"stu-trig-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    await session.execute(
        text(
            """
            INSERT INTO leas (id, name, lea_type, state)
            VALUES (:id, 't', 'traditional_district', 'XX')
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
                :id, :lea, 'edlink', 'success', :now, :now, 0
            )
            """
        ),
        {"id": sync_job_id, "lea": lea_id, "now": now},
    )
    await session.execute(
        text(
            """
            INSERT INTO students (
                id, lea_id, given_name, family_name, grade, external_ids
            ) VALUES (
                :sid, :lea, 'T', 'Estable', '05',
                CAST('{}' AS JSONB)
            )
            """
        ),
        {"sid": student_id, "lea": lea_id},
    )
    await session.execute(
        text(
            """
            INSERT INTO student_snapshots (
                snapshot_id, student_id, lea_id, generation_id,
                deleted_upstream, source_event_id, source_event_at,
                created_at, payload
            ) VALUES (
                :snap_id, :stu_id, :lea, :gen, false,
                'evt_001', :now, :now, CAST(:payload AS JSONB)
            )
            """
        ),
        {
            "snap_id": snapshot_id,
            "stu_id": student_id,
            "lea": lea_id,
            "gen": sync_job_id,
            "now": now,
            "payload": json.dumps({"grade": "05"}),
        },
    )
    return sync_job_id, snapshot_id, student_id


@pytest_asyncio.fixture
async def trigger_seed(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Seed one LEA + one student snapshot, then wipe on teardown.

    Trigger tests share this fixture so the demo runner never sees
    leftover ``lea-trig-*`` rows.
    """

    lea_id = LeaId(f"lea-trig-{uuid.uuid4().hex[:8]}")
    async with db_session_factory() as session:
        sync_job_id, snapshot_id, student_id = await _seed_lea_and_snapshot(
            session, lea_id
        )
        await session.commit()

    yield lea_id, sync_job_id, snapshot_id, student_id

    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()


@pytest.mark.asyncio
async def test_supersession_update_succeeds(
    db_session_factory: async_sessionmaker[Any],
    trigger_seed: tuple[LeaId, uuid.UUID, uuid.UUID, str],
) -> None:
    """The supersession columns are the only legal UPDATE target."""

    _, sync_job_id, snapshot_id, _ = trigger_seed
    async with db_session_factory() as session:
        now = datetime.now(UTC)
        await session.execute(
            text(
                """
                UPDATE student_snapshots
                SET superseded_by_generation_id = :gen,
                    superseded_at = :now
                WHERE snapshot_id = :sid
                """
            ),
            {"gen": sync_job_id, "now": now, "sid": snapshot_id},
        )
        await session.commit()

        row = (
            await session.execute(
                text(
                    """
                    SELECT superseded_by_generation_id, superseded_at
                    FROM student_snapshots WHERE snapshot_id = :sid
                    """
                ),
                {"sid": snapshot_id},
            )
        ).one()
        assert row.superseded_by_generation_id == sync_job_id
        assert row.superseded_at is not None


@pytest.mark.asyncio
async def test_payload_update_raises(
    db_session_factory: async_sessionmaker[Any],
    trigger_seed: tuple[LeaId, uuid.UUID, uuid.UUID, str],
) -> None:
    """Attempting to change the snapshot payload fires the trigger."""

    _, _, snapshot_id, _ = trigger_seed
    async with db_session_factory() as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    """
                    UPDATE student_snapshots
                    SET payload = CAST(:p AS JSONB)
                    WHERE snapshot_id = :sid
                    """
                ),
                {
                    "p": json.dumps({"grade": "06"}),
                    "sid": snapshot_id,
                },
            )
            await session.commit()


@pytest.mark.asyncio
async def test_deleted_upstream_update_raises(
    db_session_factory: async_sessionmaker[Any],
    trigger_seed: tuple[LeaId, uuid.UUID, uuid.UUID, str],
) -> None:
    """Changing deleted_upstream after the fact also raises."""

    _, _, snapshot_id, _ = trigger_seed
    async with db_session_factory() as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    """
                    UPDATE student_snapshots
                    SET deleted_upstream = true
                    WHERE snapshot_id = :sid
                    """
                ),
                {"sid": snapshot_id},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_source_event_id_update_raises(
    db_session_factory: async_sessionmaker[Any],
    trigger_seed: tuple[LeaId, uuid.UUID, uuid.UUID, str],
) -> None:
    """source_event_id is part of the immutable contract too."""

    _, _, snapshot_id, _ = trigger_seed
    async with db_session_factory() as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    """
                    UPDATE student_snapshots
                    SET source_event_id = 'tampered'
                    WHERE snapshot_id = :sid
                    """
                ),
                {"sid": snapshot_id},
            )
            await session.commit()
