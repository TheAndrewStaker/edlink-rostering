"""Integration tests for the soft-delete revert service.

The deep cut of the POC. Reverting a sync_job:

- Undoes its writes without hard-deleting (preserves the audit trail).
- Restores prior snapshots to live status.
- Rewinds canonical to the prior snapshot's payload, or soft-deletes the
  canonical row if no prior snapshot existed.
- Is idempotent: calling revert twice produces the same final state,
  with the second call writing an audit row carrying
  ``snapshots_restored = 0``.
- Refuses to revert a sync that has been superseded by a newer sync.

Setup pattern: drive the fixture LEA through the sync worker in two
batches (page_size = 6), so batch A creates the initial roster
(evt_001-006) and batch B applies an update + a deletion (evt_007-008).
Revert batch B and verify canonical state matches the post-batch-A
snapshot.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.connectors.edlink import EdLinkClient, EdLinkConnector
from edlink_rostering.core.types import LeaId
from edlink_rostering.infrastructure.azure_mocks import KeyVaultClient
from edlink_rostering.infrastructure.azure_mocks.app_insights import (
    MemorySink,
    Telemetry,
)
from edlink_rostering.services.revert import (
    RevertRefused,
    RevertService,
    RevertSyncJobNotFound,
)
from edlink_rostering.services.sync_worker import PageOutcome, SyncWorker


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fixture_lea_id() -> LeaId:
    return LeaId("lea-test-001")


@pytest.fixture
def telemetry() -> Telemetry:
    return Telemetry(sinks=[MemorySink()])


@pytest.fixture
def two_batch_worker(
    edlink_fixtures_dir: Path,
    db_session_factory: async_sessionmaker[Any],
    telemetry: Telemetry,
    fixture_lea_id: LeaId,
) -> SyncWorker:
    """Sync worker bound to a connector with page_size=6.

    The fixture has 8 events. With page_size=6, the first drain returns a
    page of 6 with has_more=True; the second drain returns the remaining
    2 with has_more=False. That gives us two distinct sync_jobs to play
    revert against.
    """

    vault = KeyVaultClient()
    vault.put_secret(f"edlink-token-{fixture_lea_id}", "bearer-fake")
    connector = EdLinkConnector(
        client=EdLinkClient(fixtures_dir=edlink_fixtures_dir),
        key_vault=vault,
        session_factory=db_session_factory,
        page_size=6,
    )
    return SyncWorker(
        connector=connector,
        session_factory=db_session_factory,
        telemetry=telemetry,
    )


@pytest_asyncio.fixture
async def cleanup(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    leas_to_clean: list[LeaId] = []

    def register(lea_id: LeaId) -> None:
        leas_to_clean.append(lea_id)

    yield register

    if not leas_to_clean:
        return
    async with db_session_factory() as session:
        for lea_id in leas_to_clean:
            await _wipe_lea(session, lea_id)
        await session.commit()


@pytest_asyncio.fixture
async def two_batches(
    two_batch_worker: SyncWorker,
    fixture_lea_id: LeaId,
    cleanup: Any,
    db_session_factory: async_sessionmaker[Any],
) -> tuple[PageOutcome, PageOutcome]:
    """Drain the fixture in two passes. Returns (batch_A, batch_B) where
    batch_A holds evt_001-006 and batch_B holds evt_007-008.

    Pre-wipes the LEA so leftover state from the demo runner or a prior
    test does not contaminate the fixture; registers cleanup so the
    finalizer wipes after the test body finishes."""

    cleanup(fixture_lea_id)
    async with db_session_factory() as session:
        await _wipe_lea(session, fixture_lea_id)
        await session.commit()

    outcomes = await two_batch_worker.drain_lea(fixture_lea_id)
    assert len(outcomes) == 2
    assert outcomes[0].cursor_after == "evt_006"
    assert outcomes[0].event_count == 6
    assert outcomes[1].cursor_after == "evt_008"
    assert outcomes[1].event_count == 2
    return outcomes[0], outcomes[1]


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revert_batch_b_restores_alex_grade_and_undeletes_carmen(
    db_session_factory: async_sessionmaker[Any],
    two_batches: tuple[PageOutcome, PageOutcome],
) -> None:
    _, batch_b = two_batches

    # Confirm pre-revert state matches expectations.
    async with db_session_factory() as session:
        alex = (
            await session.execute(
                text(
                    "SELECT grade, deleted_at FROM students WHERE id = 'stu-001'"
                ),
            )
        ).one()
        assert alex.grade == "06"

        carmen = (
            await session.execute(
                text(
                    "SELECT grade, deleted_at FROM students WHERE id = 'stu-003'"
                ),
            )
        ).one()
        assert carmen.deleted_at is not None

    service = RevertService(session_factory=db_session_factory)
    outcome = await service.revert(
        sync_job_id=batch_b.sync_job_id,
        operator_identity="stephen@edlink.test",
        reason="round-two demo: replay the demo flow",
    )

    # Batch B touched 2 entities (stu-001 update, stu-003 deletion).
    # Both had prior snapshots, so both are restored, not soft-deleted.
    assert outcome.snapshots_restored == 2
    assert outcome.canonical_rows_updated == 2
    assert outcome.canonical_rows_soft_deleted == 0

    async with db_session_factory() as session:
        alex_after = (
            await session.execute(
                text(
                    "SELECT grade, deleted_at FROM students WHERE id = 'stu-001'"
                ),
            )
        ).one()
        assert alex_after.grade == "05"
        assert alex_after.deleted_at is None

        carmen_after = (
            await session.execute(
                text(
                    "SELECT grade, deleted_at FROM students WHERE id = 'stu-003'"
                ),
            )
        ).one()
        assert carmen_after.deleted_at is None
        assert carmen_after.grade == "05"

        # Prior snapshots (evt_001 for Alex, evt_003 for Carmen) should be
        # live again, evt_007 and evt_008 marked superseded by the revert.
        live_alex_source = (
            await session.execute(
                text(
                    """
                    SELECT source_event_id FROM student_snapshots
                    WHERE student_id = 'stu-001'
                      AND superseded_by_generation_id IS NULL
                    """
                ),
            )
        ).one()
        assert live_alex_source.source_event_id == "evt_001"

        live_carmen_source = (
            await session.execute(
                text(
                    """
                    SELECT source_event_id, deleted_upstream
                    FROM student_snapshots
                    WHERE student_id = 'stu-003'
                      AND superseded_by_generation_id IS NULL
                    """
                ),
            )
        ).one()
        assert live_carmen_source.source_event_id == "evt_003"
        assert live_carmen_source.deleted_upstream is False

        # evt_007 (Alex update) and evt_008 (Carmen deletion) now point at
        # the revert_generation_id.
        reverted_alex = (
            await session.execute(
                text(
                    """
                    SELECT superseded_by_generation_id
                    FROM student_snapshots
                    WHERE student_id = 'stu-001'
                      AND source_event_id = 'evt_007'
                    """
                ),
            )
        ).one()
        assert (
            reverted_alex.superseded_by_generation_id
            == outcome.revert_generation_id
        )

        # revert_actions row exists with snapshots_restored = 2.
        action = (
            await session.execute(
                text(
                    """
                    SELECT operator_identity, reason, snapshots_restored
                    FROM revert_actions
                    WHERE sync_job_id = :j
                    """
                ),
                {"j": batch_b.sync_job_id},
            )
        ).one()
        assert action.operator_identity == "stephen@edlink.test"
        assert action.snapshots_restored == 2


# ── Idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revert_twice_is_idempotent_and_audited(
    db_session_factory: async_sessionmaker[Any],
    two_batches: tuple[PageOutcome, PageOutcome],
) -> None:
    _, batch_b = two_batches
    service = RevertService(session_factory=db_session_factory)

    first = await service.revert(
        sync_job_id=batch_b.sync_job_id,
        operator_identity="ops@edlink.test",
        reason="first revert",
    )
    assert first.snapshots_restored == 2

    second = await service.revert(
        sync_job_id=batch_b.sync_job_id,
        operator_identity="ops@edlink.test",
        reason="accidental double-click",
    )
    assert second.snapshots_restored == 0
    assert second.canonical_rows_updated == 0
    assert second.canonical_rows_soft_deleted == 0
    assert second.revert_id != first.revert_id

    async with db_session_factory() as session:
        actions = (
            await session.execute(
                text(
                    """
                    SELECT snapshots_restored, reason
                    FROM revert_actions WHERE sync_job_id = :j
                    ORDER BY reverted_at ASC
                    """
                ),
                {"j": batch_b.sync_job_id},
            )
        ).all()
        assert len(actions) == 2
        assert actions[0].snapshots_restored == 2
        assert actions[1].snapshots_restored == 0
        assert actions[1].reason == "accidental double-click"


# ── Soft delete when no prior snapshot exists ─────────────────────────────────


@pytest.mark.asyncio
async def test_revert_batch_a_soft_deletes_freshly_created_entities(
    db_session_factory: async_sessionmaker[Any],
    two_batches: tuple[PageOutcome, PageOutcome],
) -> None:
    """Batch A created stu-002 and Bryn's enrollment from scratch; batch
    B did not touch them. Reverting batch A while batch B still stands
    is refused because batch B's snapshots reference batch A's state."""

    batch_a, _ = two_batches
    service = RevertService(session_factory=db_session_factory)

    with pytest.raises(RevertRefused):
        await service.revert(
            sync_job_id=batch_a.sync_job_id,
            operator_identity="ops@edlink.test",
            reason="trying to revert too far back",
        )


@pytest.mark.asyncio
async def test_revert_batch_a_after_batch_b_revert_soft_deletes_creations(
    db_session_factory: async_sessionmaker[Any],
    two_batches: tuple[PageOutcome, PageOutcome],
) -> None:
    """Revert batch B first (so batch A's snapshots have no later sync
    on top of them), then revert batch A. Batch A's 6 entities (3
    students + 3 enrollments) have no prior snapshots, so revert
    soft-deletes them."""

    batch_a, batch_b = two_batches
    service = RevertService(session_factory=db_session_factory)
    await service.revert(
        sync_job_id=batch_b.sync_job_id,
        operator_identity="ops@edlink.test",
        reason="undo updates",
    )

    outcome_a = await service.revert(
        sync_job_id=batch_a.sync_job_id,
        operator_identity="ops@edlink.test",
        reason="wipe the demo roster",
    )

    # 6 snapshots from batch A, all freshly created, all soft-deleted.
    assert outcome_a.snapshots_restored == 6
    assert outcome_a.canonical_rows_soft_deleted == 6
    assert outcome_a.canonical_rows_updated == 0

    async with db_session_factory() as session:
        live_students = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM students
                    WHERE lea_id = 'lea-test-001' AND deleted_at IS NULL
                    """
                ),
            )
        ).scalar_one()
        assert live_students == 0


# ── Refuses bad inputs ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revert_unknown_sync_job_raises(
    db_session_factory: async_sessionmaker[Any],
) -> None:
    service = RevertService(session_factory=db_session_factory)
    with pytest.raises(RevertSyncJobNotFound):
        await service.revert(
            sync_job_id=uuid.uuid4(),
            operator_identity="ops@edlink.test",
            reason="bogus id",
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _wipe_lea(session: Any, lea_id: LeaId) -> None:
    for table in (
        "sync_validation_results",
        "revert_actions",
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
    for table in (
        "enrollments",
        "students",
        "sync_jobs",
        "cursor_state",
        "leas",
    ):
        await session.execute(
            text(
                f"DELETE FROM {table} WHERE "
                f"{'id' if table == 'leas' else 'lea_id'} = :l"
            ),
            {"l": lea_id},
        )
