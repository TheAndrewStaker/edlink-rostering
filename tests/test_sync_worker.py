"""Integration tests for the page-per-transaction sync worker.

Covers the four behaviors that the Step 4 plan requires:

1. **Vertical slice.** Drain the fixture LEA through a real Postgres.
   Snapshots, canonical rows, audit rows, and cursor advance all land in
   the right shape.
2. **Idempotency.** Re-process the same page after a cursor rewind and
   confirm zero new snapshots are written (each natural key's
   ``source_event_id`` high-water mark dedupes the replay).
3. **Deletion.** A ``person.deleted`` event sets ``deleted_at`` on the
   canonical row and ``deleted_upstream = true`` on the new snapshot.
4. **Orphan quarantine.** An enrollment whose student does not exist
   anywhere goes to the ``quarantine`` table, the rest of the batch
   commits, and the cursor advances normally.

DB-bound tests skip if ``APP_DATABASE_URL`` is not set; the test runner
uses ``scripts/test.sh`` which sources ``.env`` (or
``.env.example``) into the environment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.canonical.entities import Enrollment, EntityType, Student
from edlink_rostering.connectors.edlink import EdLinkClient, EdLinkConnector
from edlink_rostering.connectors.protocol import EventPage, Layer1Result
from edlink_rostering.core.types import Cursor, EnrollmentId, EventId, LeaId, StudentId
from edlink_rostering.events.envelope import NormalizedEvent, Operation
from edlink_rostering.infrastructure.azure_mocks import KeyVaultClient
from edlink_rostering.infrastructure.azure_mocks.app_insights import (
    MemorySink,
    Telemetry,
)
from edlink_rostering.services.sync_worker import SyncWorker


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def lea_id() -> LeaId:
    """Unique LEA per test so the DB state stays isolated across runs."""

    return LeaId(f"lea-sync-test-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def telemetry() -> Telemetry:
    """Telemetry with a memory sink for assertions on emitted events."""

    return Telemetry(sinks=[MemorySink()])


@pytest.fixture
def telemetry_sink(telemetry: Telemetry) -> MemorySink:
    sink = telemetry._sinks[0]
    assert isinstance(sink, MemorySink)
    return sink


@pytest.fixture
def key_vault(lea_id: LeaId) -> KeyVaultClient:
    """Key Vault with a token staged for the test LEA."""

    vault = KeyVaultClient()
    vault.put_secret(f"edlink-token-{lea_id}", "bearer-fake")
    return vault


@pytest.fixture
def fixture_lea_id() -> LeaId:
    """The LEA whose timeline the fixture file describes.

    Used when we want to point the worker at the actual fixture data
    (which is keyed to ``lea-test-001``) rather than a random per-test
    LEA. The worker's natural-key isolation relies on lea_id, so two
    tests using ``lea-test-001`` simultaneously would clash. Tests that
    use this fixture clean up after themselves.
    """

    return LeaId("lea-test-001")


@pytest.fixture
def fixture_worker(
    edlink_fixtures_dir: Path,
    db_session_factory: async_sessionmaker[Any],
    telemetry: Telemetry,
    fixture_lea_id: LeaId,
) -> SyncWorker:
    vault = KeyVaultClient()
    vault.put_secret(f"edlink-token-{fixture_lea_id}", "bearer-fake")
    connector = EdLinkConnector(
        client=EdLinkClient(fixtures_dir=edlink_fixtures_dir),
        key_vault=vault,
        session_factory=db_session_factory,
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
    """Cleanup hook the test calls with the LEA(s) it touched.

    Implemented as an async-generator fixture so the teardown can run
    against the real Postgres session pool after the test body returns.
    """

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


# ── Vertical slice ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drain_writes_snapshots_canonical_audit_and_cursor(
    fixture_worker: SyncWorker,
    fixture_lea_id: LeaId,
    db_session_factory: async_sessionmaker[Any],
    cleanup: Any,
) -> None:
    cleanup(fixture_lea_id)
    async with db_session_factory() as session:
        await _wipe_lea(session, fixture_lea_id)
        await session.commit()

    outcomes = await fixture_worker.drain_lea(fixture_lea_id)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.status == "success"
    assert outcome.cursor_before == ""
    assert outcome.cursor_after == "evt_008"
    assert outcome.event_count == 8
    assert outcome.has_more is False

    async with db_session_factory() as session:
        # Canonical state: 3 students (one soft-deleted), 3 enrollments,
        # 1 LEA (auto-bootstrap placeholder).
        student_count = _scalar(
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM students WHERE lea_id = :lea"
                ),
                {"lea": fixture_lea_id},
            )
        )
        assert student_count == 3
        live_students = _scalar(
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM students "
                    "WHERE lea_id = :lea AND deleted_at IS NULL"
                ),
                {"lea": fixture_lea_id},
            )
        )
        assert live_students == 2

        enrollment_count = _scalar(
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM enrollments WHERE lea_id = :lea"
                ),
                {"lea": fixture_lea_id},
            )
        )
        assert enrollment_count == 3

        # Alex's grade was 05 then updated to 06; canonical reflects the
        # latest applied event.
        alex_grade = _scalar(
            await session.execute(
                text("SELECT grade FROM students WHERE id = 'stu-001'"),
            )
        )
        assert alex_grade == "06"

        # Snapshot history: one row per event => 8 snapshot rows total
        # across students (5: evt_001, 002, 003, 007, 008) and
        # enrollments (3: evt_004, 005, 006).
        student_snapshots = _scalar(
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM student_snapshots "
                    "WHERE lea_id = :lea"
                ),
                {"lea": fixture_lea_id},
            )
        )
        assert student_snapshots == 5
        enrollment_snapshots = _scalar(
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM enrollment_snapshots "
                    "WHERE lea_id = :lea"
                ),
                {"lea": fixture_lea_id},
            )
        )
        assert enrollment_snapshots == 3

        # Alex's evt_001 snapshot was superseded by evt_007's snapshot
        # (the grade change). Both should share generation_id since
        # they were processed by the same sync job.
        prior_alex = (
            await session.execute(
                text(
                    """
                    SELECT superseded_by_generation_id, source_event_id
                    FROM student_snapshots
                    WHERE student_id = 'stu-001'
                      AND source_event_id = 'evt_001'
                    """
                ),
            )
        ).one()
        assert prior_alex.superseded_by_generation_id == outcome.sync_job_id
        live_alex = (
            await session.execute(
                text(
                    """
                    SELECT source_event_id, deleted_upstream
                    FROM student_snapshots
                    WHERE student_id = 'stu-001'
                      AND superseded_by_generation_id IS NULL
                    """
                ),
            )
        ).one()
        assert live_alex.source_event_id == "evt_007"
        assert live_alex.deleted_upstream is False

        # Audit: exactly one sync_jobs row in 'success' status.
        sync_jobs = (
            await session.execute(
                text(
                    """
                    SELECT id, status, event_count, cursor_before, cursor_after
                    FROM sync_jobs WHERE lea_id = :lea
                    """
                ),
                {"lea": fixture_lea_id},
            )
        ).all()
        assert len(sync_jobs) == 1
        job = sync_jobs[0]
        assert job.status == "success"
        assert job.event_count == 8
        assert job.cursor_before is None or job.cursor_before == ""
        assert job.cursor_after == "evt_008"

        # Cursor advanced to evt_008.
        cursor_row = (
            await session.execute(
                text(
                    """
                    SELECT last_event_id FROM cursor_state
                    WHERE lea_id = :lea AND partner = 'edlink'
                    """
                ),
                {"lea": fixture_lea_id},
            )
        ).one()
        assert cursor_row.last_event_id == "evt_008"

        # Validation results: at least one Layer 5 informational record.
        layer_5_count = _scalar(
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM sync_validation_results
                    WHERE sync_job_id = :job_id AND layer = 5
                    """
                ),
                {"job_id": outcome.sync_job_id},
            )
        )
        assert layer_5_count >= 1


# ── Deletion ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deletion_marks_canonical_and_snapshot(
    fixture_worker: SyncWorker,
    fixture_lea_id: LeaId,
    db_session_factory: async_sessionmaker[Any],
    cleanup: Any,
) -> None:
    cleanup(fixture_lea_id)
    async with db_session_factory() as session:
        await _wipe_lea(session, fixture_lea_id)
        await session.commit()
    await fixture_worker.drain_lea(fixture_lea_id)

    async with db_session_factory() as session:
        carmen = (
            await session.execute(
                text(
                    "SELECT deleted_at FROM students WHERE id = 'stu-003'"
                ),
            )
        ).one()
        assert carmen.deleted_at is not None

        live_carmen_snapshot = (
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
        assert live_carmen_snapshot.source_event_id == "evt_008"
        assert live_carmen_snapshot.deleted_upstream is True


# ── Idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_after_cursor_rewind_produces_zero_new_snapshots(
    fixture_worker: SyncWorker,
    fixture_lea_id: LeaId,
    db_session_factory: async_sessionmaker[Any],
    cleanup: Any,
) -> None:
    cleanup(fixture_lea_id)
    async with db_session_factory() as session:
        await _wipe_lea(session, fixture_lea_id)
        await session.commit()

    # First drain: writes everything.
    first = await fixture_worker.drain_lea(fixture_lea_id)
    assert first[0].event_count == 8

    async with db_session_factory() as session:
        snapshots_after_first = _scalar(
            await session.execute(
                text(
                    """
                    SELECT (
                      (SELECT COUNT(*) FROM student_snapshots WHERE lea_id = :l)
                      +
                      (SELECT COUNT(*) FROM enrollment_snapshots WHERE lea_id = :l)
                    ) AS total
                    """
                ),
                {"l": fixture_lea_id},
            )
        )
        students_after_first = _scalar(
            await session.execute(
                text("SELECT COUNT(*) FROM students WHERE lea_id = :l"),
                {"l": fixture_lea_id},
            )
        )

    # Rewind cursor to empty.
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                UPDATE cursor_state SET last_event_id = NULL
                WHERE lea_id = :l AND partner = 'edlink'
                """
            ),
            {"l": fixture_lea_id},
        )
        await session.commit()

    # Second drain: every event is now a replay; no new snapshots should land.
    second = await fixture_worker.drain_lea(fixture_lea_id)
    assert second[0].status == "success"
    assert second[0].event_count == 0
    assert second[0].skipped_count == 8

    async with db_session_factory() as session:
        snapshots_after_second = _scalar(
            await session.execute(
                text(
                    """
                    SELECT (
                      (SELECT COUNT(*) FROM student_snapshots WHERE lea_id = :l)
                      +
                      (SELECT COUNT(*) FROM enrollment_snapshots WHERE lea_id = :l)
                    ) AS total
                    """
                ),
                {"l": fixture_lea_id},
            )
        )
        students_after_second = _scalar(
            await session.execute(
                text("SELECT COUNT(*) FROM students WHERE lea_id = :l"),
                {"l": fixture_lea_id},
            )
        )

    assert snapshots_after_second == snapshots_after_first
    assert students_after_second == students_after_first

    # Two sync_jobs rows in 'success' status (one per drain). Both write
    # an audit trail even when the second is a no-op.
    async with db_session_factory() as session:
        success_count = _scalar(
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM sync_jobs
                    WHERE lea_id = :l AND status = 'success'
                    """
                ),
                {"l": fixture_lea_id},
            )
        )
    assert success_count == 2


# ── Orphan quarantine ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orphan_enrollment_goes_to_quarantine_batch_commits(
    db_session_factory: async_sessionmaker[Any],
    telemetry: Telemetry,
    cleanup: Any,
) -> None:
    """A synthetic page with one student and one enrollment-of-a-missing
    student. Layer 4 quarantines the enrollment; the student still
    lands; the batch advances the cursor."""

    lea_id = LeaId(f"lea-quarantine-{uuid.uuid4().hex[:8]}")
    cleanup(lea_id)

    page = _build_synthetic_page(lea_id)
    connector = _StaticPageConnector(page=page, name="edlink")
    worker = SyncWorker(
        connector=connector,
        session_factory=db_session_factory,
        telemetry=telemetry,
    )

    outcomes = await worker.drain_lea(lea_id)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.status == "success"
    assert outcome.event_count == 1
    assert outcome.quarantined_count == 1
    assert outcome.cursor_after == "evt_500"

    async with db_session_factory() as session:
        # Student is in canonical.
        student_count = _scalar(
            await session.execute(
                text("SELECT COUNT(*) FROM students WHERE lea_id = :l"),
                {"l": lea_id},
            )
        )
        assert student_count == 1

        # Enrollment is NOT in canonical.
        enrollment_count = _scalar(
            await session.execute(
                text("SELECT COUNT(*) FROM enrollments WHERE lea_id = :l"),
                {"l": lea_id},
            )
        )
        assert enrollment_count == 0

        # Quarantine row exists with the right reason.
        quarantine = (
            await session.execute(
                text(
                    """
                    SELECT entity_type, entity_id, reason
                    FROM quarantine WHERE lea_id = :l
                    """
                ),
                {"l": lea_id},
            )
        ).all()
        assert len(quarantine) == 1
        assert quarantine[0].entity_type == "enrollment"
        assert quarantine[0].entity_id == "enr-orphan-001"
        assert "Layer 4" in quarantine[0].reason


# ── Helpers ───────────────────────────────────────────────────────────────────


def _scalar(result: Any) -> Any:
    """Pull the first scalar value out of a one-row, one-column result."""

    row = result.one()
    return row[0]


async def _wipe_lea(session: Any, lea_id: LeaId) -> None:
    """Tear down all rows touching an LEA so the test is repeatable.

    Uses raw SQL to avoid coupling to the ORM identity map and to allow
    DELETE on tables the app role normally cannot delete from (the
    test runs as the same role, so this stays inside the application's
    permission boundary in CI; production retention uses edlink_dba).
    """

    for table in (
        "sync_validation_results",
        "revert_actions",
        "quarantine",
    ):
        await session.execute(
            text(f"DELETE FROM {table} WHERE sync_job_id IN "
                 f"(SELECT id FROM sync_jobs WHERE lea_id = :l)"),
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
            text(f"DELETE FROM {table} WHERE "
                 f"{'id' if table == 'leas' else 'lea_id'} = :l"),
            {"l": lea_id},
        )


class _StaticPageConnector:
    """Test double that hands the same page on every fetch.

    Implements the subset of :class:`Connector` the sync worker actually
    calls: ``name``, ``fetch_changes``. The worker's ``drain_lea`` loop
    will stop after one page because ``has_more=False`` on the static
    page.
    """

    def __init__(self, page: EventPage, name: str = "edlink") -> None:
        self._page = page
        self.name = name

    async def fetch_changes(
        self, lea_id: LeaId, since: Cursor
    ) -> EventPage:
        # Honor the cursor: when the worker calls again after the cursor
        # has advanced to the page's next_cursor, return an empty page.
        if since.value == self._page.next_cursor.value:
            return EventPage(
                events=[],
                next_cursor=since,
                has_more=False,
                retrieved_at=datetime.now(UTC),
                layer_1_check=self._page.layer_1_check,
            )
        return self._page


def _build_synthetic_page(lea_id: LeaId) -> EventPage:
    """One student.created plus one orphan enrollment.created."""

    student_event = NormalizedEvent(
        event_id=EventId("evt_400"),
        lea_id=lea_id,
        entity_type=EntityType.STUDENT,
        operation=Operation.CREATED,
        entity=Student(
            id=StudentId("stu-400"),
            lea_id=lea_id,
            given_name="Dana",
            family_name="Park",
            grade="07",
        ),
        source_connector="edlink",
        source_event_id="evt_400",
        occurred_at=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
        received_at=datetime.now(UTC),
    )
    orphan_enrollment = NormalizedEvent(
        event_id=EventId("evt_500"),
        lea_id=lea_id,
        entity_type=EntityType.ENROLLMENT,
        operation=Operation.CREATED,
        entity=Enrollment(
            id=EnrollmentId("enr-orphan-001"),
            lea_id=lea_id,
            student_id=StudentId("stu-MISSING"),
            class_id="cls-X",
            begin_date=date(2026, 8, 15),
            end_date=None,
        ),
        source_connector="edlink",
        source_event_id="evt_500",
        occurred_at=datetime(2026, 5, 19, 12, 0, 1, tzinfo=UTC),
        received_at=datetime.now(UTC),
    )
    return EventPage(
        events=[student_event, orphan_enrollment],
        next_cursor=Cursor(
            value="evt_500",
            observed_at=datetime(2026, 5, 19, 12, 0, 1, tzinfo=UTC),
        ),
        has_more=False,
        retrieved_at=datetime.now(UTC),
        layer_1_check=Layer1Result(
            ok=True,
            http_status=200,
            content_type="application/json",
            body_well_formed=True,
        ),
    )
