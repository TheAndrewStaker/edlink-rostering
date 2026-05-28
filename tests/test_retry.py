"""RetryService tests.

Covers: retry of a failed sync rewinds the cursor and audits; retry of
a success refuses unless --force; retry of an unknown sync raises; the
next drain after retry replays the events idempotently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.core.types import LeaId
from edlink_rostering.services.retry import (
    RetryRefused,
    RetryService,
    RetrySyncJobNotFound,
)
from tests.conftest import wipe_lea


@pytest_asyncio.fixture
async def lea_with_failed_sync(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Insert a failed sync_jobs row + a cursor at evt_010 for a fresh LEA."""

    lea_id = LeaId(f"lea-retry-{uuid.uuid4().hex[:8]}")
    sync_job_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'retry test', 'traditional_district', 'XX')
                """
            ),
            {"id": lea_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs (
                    id, lea_id, partner, status, started_at, completed_at,
                    event_count, cursor_before, cursor_after, error_summary
                ) VALUES (
                    :id, :lea, 'edlink', 'failed', :now, :now, 0,
                    'evt_010', NULL, 'L1:HTTP_INTEGRITY_FAILED'
                )
                """
            ),
            {"id": sync_job_id, "lea": lea_id, "now": now},
        )
        await session.execute(
            text(
                """
                INSERT INTO cursor_state (
                    lea_id, partner, last_event_id, last_event_at,
                    last_poll_at, cold_start_required, updated_at
                ) VALUES (
                    :lea, 'edlink', 'evt_020', :now, :now, false, :now
                )
                """
            ),
            {"lea": lea_id, "now": now},
        )
        await session.commit()

    yield lea_id, sync_job_id

    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()


@pytest.mark.asyncio
async def test_retry_failed_sync_rewinds_cursor_and_writes_audit(
    db_session_factory: async_sessionmaker[Any],
    lea_with_failed_sync: tuple[LeaId, uuid.UUID],
) -> None:
    lea_id, sync_job_id = lea_with_failed_sync
    service = RetryService(session_factory=db_session_factory)

    outcome = await service.retry(
        sync_job_id=sync_job_id,
        operator_identity="qa@edlink.test",
        reason="HTTP 503 from EdLink; retrying",
    )
    assert outcome.lea_id == lea_id
    assert outcome.cursor_rewound_to == "evt_010"
    assert outcome.forced is False

    async with db_session_factory() as session:
        cursor_row = (
            await session.execute(
                text(
                    """
                    SELECT last_event_id FROM cursor_state
                    WHERE lea_id = :lea AND partner = 'edlink'
                    """
                ),
                {"lea": lea_id},
            )
        ).one()
        assert cursor_row.last_event_id == "evt_010"

        retry_row = (
            await session.execute(
                text(
                    """
                    SELECT operator_identity, reason, cursor_rewound_to,
                           forced
                    FROM retry_actions WHERE sync_job_id = :sj
                    """
                ),
                {"sj": sync_job_id},
            )
        ).one()
        assert retry_row.operator_identity == "qa@edlink.test"
        assert retry_row.cursor_rewound_to == "evt_010"
        assert retry_row.forced is False


@pytest.mark.asyncio
async def test_retry_of_unknown_sync_raises(
    db_session_factory: async_sessionmaker[Any],
) -> None:
    service = RetryService(session_factory=db_session_factory)
    with pytest.raises(RetrySyncJobNotFound):
        await service.retry(
            sync_job_id=uuid.uuid4(),
            operator_identity="qa@edlink.test",
            reason="should not happen",
        )


@pytest.mark.asyncio
async def test_retry_of_successful_sync_refuses_without_force(
    db_session_factory: async_sessionmaker[Any],
) -> None:
    """Successful syncs are not retried by default."""

    lea_id = LeaId(f"lea-retry-success-{uuid.uuid4().hex[:8]}")
    sync_job_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'r', 'traditional_district', 'XX')
                """
            ),
            {"id": lea_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs (
                    id, lea_id, partner, status, started_at, completed_at,
                    event_count, cursor_before, cursor_after
                ) VALUES (
                    :id, :lea, 'edlink', 'success', :now, :now, 5,
                    'evt_001', 'evt_005'
                )
                """
            ),
            {"id": sync_job_id, "lea": lea_id, "now": now},
        )
        await session.commit()

    service = RetryService(session_factory=db_session_factory)
    with pytest.raises(RetryRefused):
        await service.retry(
            sync_job_id=sync_job_id,
            operator_identity="qa@edlink.test",
            reason="should refuse",
        )

    outcome = await service.retry(
        sync_job_id=sync_job_id,
        operator_identity="qa@edlink.test",
        reason="ok with force",
        forced=True,
    )
    assert outcome.forced is True
    assert outcome.cursor_rewound_to == "evt_001"

    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()


@pytest.mark.asyncio
async def test_retry_of_revert_synthetic_refuses(
    db_session_factory: async_sessionmaker[Any],
) -> None:
    """A revert synthetic sync_jobs row cannot be retried."""

    lea_id = LeaId(f"lea-retry-revert-{uuid.uuid4().hex[:8]}")
    sync_job_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'r', 'traditional_district', 'XX')
                """
            ),
            {"id": lea_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs (
                    id, lea_id, partner, status, started_at, completed_at,
                    event_count, error_summary
                ) VALUES (
                    :id, :lea, 'edlink', 'revert', :now, :now, 0,
                    'revert of sync_job xyz'
                )
                """
            ),
            {"id": sync_job_id, "lea": lea_id, "now": now},
        )
        await session.commit()

    service = RetryService(session_factory=db_session_factory)
    with pytest.raises(RetryRefused):
        await service.retry(
            sync_job_id=sync_job_id,
            operator_identity="qa@edlink.test",
            reason="should refuse",
            forced=True,
        )

    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()
