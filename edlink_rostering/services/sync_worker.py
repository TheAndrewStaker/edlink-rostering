"""Sync worker: page-per-transaction LEA-scoped batch processor.

Reads one page of events from the connector, validates it through the
five-layer pipeline, and commits the page to Postgres in a single
transaction. Loops while the page reports ``has_more`` so a backlog drains
in multiple sequential transactions, each its own LEA-scoped batch.

Transaction shape per page:

1. ``INSERT sync_jobs`` (status=running, cursor_before=prior cursor).
2. ``INSERT sync_validation_results`` for every issue Layers 1-5 reported.
3. If Layer 1 failed or any Layer 2/3 error fired at the page level,
   ``UPDATE sync_jobs`` to status=failed, commit the audit, and stop
   without advancing the cursor.
4. Otherwise, for each event in page order:
   - If a snapshot already exists for the natural key with a
     ``source_event_id >= event.source_event_id``: this event was
     processed by an earlier sync_job. Skip silently. This is what makes
     "process the same page twice" produce zero new snapshot rows.
   - If the event is on the Layer 4 quarantine list, insert a
     ``quarantine`` row and skip the canonical/snapshot writes.
   - Otherwise: mark the prior live snapshot superseded, insert a new
     snapshot (with ``generation_id = sync_job_id``,
     ``source_event_id = event.source_event_id``, and
     ``deleted_upstream`` set from the operation), upsert the canonical
     row (or set ``deleted_at`` for deletions).
5. ``UPDATE sync_jobs`` to status=success with counts and
   ``cursor_after = page.next_cursor.value``.
6. ``UPSERT cursor_state`` with the new cursor.

The cursor advance lives inside the same transaction as the snapshot
writes. If the transaction rolls back, the cursor stays at the prior
value and the next poll retries the same page.

The sync worker is intentionally session-bound rather than message-bound:
:meth:`drain_lea` is the public entry point. A separate Service Bus
adapter pulls messages from the queue and calls ``drain_lea`` for each
session_id. Splitting the responsibilities lets tests exercise the worker
without spinning up a Service Bus mock.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.canonical.entities import (
    Enrollment,
    EntityType,
    Lea,
    LeaType,
    Student,
)
from edlink_rostering.canonical.entities import (
    CanonicalEntity,  # for typing
)
from edlink_rostering.connectors.protocol import Connector
from edlink_rostering.core.types import Cursor, LeaId, StudentId
from edlink_rostering.events.envelope import NormalizedEvent, Operation
from edlink_rostering.infrastructure.ports import TelemetryFacade
from edlink_rostering.services.alerts import AlertService
from edlink_rostering.services.validation import (
    LEAState,
    Severity,
    ValidationReport,
    run_pipeline,
)


@dataclass(frozen=True)
class PageOutcome:
    """Result of processing one page.

    The CLI's ``show-sync`` command surfaces these fields. ``has_more`` is
    forwarded to the caller so the drain loop knows when to stop.
    """

    sync_job_id: uuid.UUID
    lea_id: LeaId
    status: str
    cursor_before: str
    cursor_after: str
    event_count: int
    skipped_count: int
    quarantined_count: int
    error_count: int
    warning_count: int
    has_more: bool


class SyncWorker:
    """Drives one connector against one Postgres."""

    def __init__(
        self,
        connector: Connector,
        session_factory: async_sessionmaker[AsyncSession],
        telemetry: TelemetryFacade | None = None,
        max_pages_per_drain: int = 100,
        alerts: AlertService | None = None,
        integration_status_poller: Any = None,
    ) -> None:
        self._connector = connector
        self._sessions = session_factory
        self._telemetry = telemetry
        self._max_pages = max_pages_per_drain
        self._alerts = alerts
        self._integration_status_poller = integration_status_poller

    async def drain_lea(self, lea_id: LeaId) -> list[PageOutcome]:
        """Process every available page for one LEA.

        Hard-cap at ``max_pages_per_drain`` so a runaway producer cannot
        starve other LEAs in the same worker process. The cap is a safety
        net: production sizing keeps a single LEA's per-poll backlog far
        below the default.

        Before the page loop, polls the partner's integration status
        via the optional :class:`IntegrationStatusPoller`. A degraded
        integration (``inactive``/``disabled``/``destroyed``) skips
        the drain entirely so the worker does not burn polls against
        a revoked or paused token. The poller persists the snapshot
        on ``connector_authorization`` and fires the
        ``integration_degraded`` alert on each call where the partner
        reports a degraded state.
        """

        if self._integration_status_poller is not None:
            record = await self._integration_status_poller.poll_and_persist(
                lea_id
            )
            if record is not None and record.is_degraded:
                # Skip the drain; the alert was already fired by the
                # poller. The caller sees an empty outcome list and
                # the next scheduled poll will re-check the status.
                return []

        outcomes: list[PageOutcome] = []
        for _ in range(self._max_pages):
            outcome, report, error_summary = await self._process_one_page(
                lea_id
            )
            outcomes.append(outcome)
            if self._telemetry is not None:
                self._telemetry.track_event(
                    "sync.page_processed",
                    properties={
                        "lea_id": lea_id,
                        "partner": self._connector.name,
                        "status": outcome.status,
                        "sync_job_id": str(outcome.sync_job_id),
                    },
                    measurements={
                        "event_count": float(outcome.event_count),
                        "skipped_count": float(outcome.skipped_count),
                        "quarantined_count": float(outcome.quarantined_count),
                        "error_count": float(outcome.error_count),
                    },
                )
            if self._alerts is not None:
                self._alerts.evaluate_sync_outcome(
                    sync_job_id=outcome.sync_job_id,
                    lea_id=lea_id,
                    partner=self._connector.name,
                    status=outcome.status,
                    report=report,
                    error_summary=error_summary,
                )
            if not outcome.has_more or outcome.status != "success":
                break
        return outcomes

    async def _process_one_page(
        self, lea_id: LeaId
    ) -> tuple[PageOutcome, ValidationReport, str | None]:
        async with self._sessions() as session:
            cursor_before = await _read_cursor(
                session, lea_id, self._connector.name
            )
            page = await self._connector.fetch_changes(
                lea_id, since=cursor_before
            )

            known_students = await _read_known_student_ids(session, lea_id)
            live_count = await _read_live_student_count(session, lea_id)
            event_history, deletion_history = await _read_recent_sync_history(
                session, lea_id, self._connector.name
            )
            lea_state = LEAState(
                lea_id=lea_id,
                known_student_ids=known_students,
                live_student_count=live_count,
                recent_event_counts=event_history,
                recent_deletion_counts=deletion_history,
            )
            report = run_pipeline(page, lea_state)

            sync_job_id = uuid.uuid4()
            now = datetime.now(UTC)
            cursor_after_value = page.next_cursor.value

            await _insert_sync_job_running(
                session=session,
                sync_job_id=sync_job_id,
                lea_id=lea_id,
                partner=self._connector.name,
                cursor_before=cursor_before.value,
                started_at=now,
            )
            await _persist_validation_issues(
                session=session,
                sync_job_id=sync_job_id,
                report=report,
                created_at=now,
            )

            if report.page_blocked:
                error_summary = _summarize_blocking_errors(report)
                await _mark_sync_job_failed(
                    session=session,
                    sync_job_id=sync_job_id,
                    completed_at=now,
                    error_count=report.error_count,
                    warning_count=report.warning_count,
                    error_summary=error_summary,
                )
                await session.commit()
                return (
                    PageOutcome(
                        sync_job_id=sync_job_id,
                        lea_id=lea_id,
                        status="failed",
                        cursor_before=cursor_before.value,
                        cursor_after=cursor_before.value,
                        event_count=0,
                        skipped_count=0,
                        quarantined_count=0,
                        error_count=report.error_count,
                        warning_count=report.warning_count,
                        has_more=False,
                    ),
                    report,
                    error_summary,
                )

            applied = 0
            skipped = 0
            quarantined = 0
            await _ensure_lea_exists(session, lea_id)
            for event in page.events:
                if event.event_id in report.quarantined_event_ids:
                    await _write_quarantine_row(
                        session=session,
                        sync_job_id=sync_job_id,
                        lea_id=lea_id,
                        event=event,
                        created_at=now,
                    )
                    quarantined += 1
                    continue
                if event.event_id not in report.ok_event_ids:
                    # Rejected by Layer 2/3 but the page is not page-blocked
                    # (the issues were per-event). Skip the canonical write
                    # and let the audit row carry the issue for operators.
                    skipped += 1
                    continue

                changed = await _apply_event(
                    session=session,
                    sync_job_id=sync_job_id,
                    lea_id=lea_id,
                    event=event,
                    created_at=now,
                )
                if changed:
                    applied += 1
                else:
                    skipped += 1

            await _mark_sync_job_success(
                session=session,
                sync_job_id=sync_job_id,
                completed_at=now,
                event_count=applied,
                error_count=report.error_count,
                warning_count=report.warning_count,
                cursor_after=cursor_after_value,
            )
            await _upsert_cursor_state(
                session=session,
                lea_id=lea_id,
                partner=self._connector.name,
                cursor=page.next_cursor,
                last_poll_at=now,
            )
            await session.commit()

            return (
                PageOutcome(
                    sync_job_id=sync_job_id,
                    lea_id=lea_id,
                    status="success",
                    cursor_before=cursor_before.value,
                    cursor_after=cursor_after_value,
                    event_count=applied,
                    skipped_count=skipped,
                    quarantined_count=quarantined,
                    error_count=report.error_count,
                    warning_count=report.warning_count,
                    has_more=page.has_more,
                ),
                report,
                None,
            )


# ── Read helpers ──────────────────────────────────────────────────────────────


async def _read_cursor(
    session: AsyncSession, lea_id: LeaId, partner: str
) -> Cursor:
    """Read the cursor row for an LEA. Returns an empty cursor if absent."""

    row = (
        await session.execute(
            text(
                """
                SELECT last_event_id, last_event_at
                FROM cursor_state
                WHERE lea_id = :lea_id AND partner = :partner
                """
            ),
            {"lea_id": lea_id, "partner": partner},
        )
    ).first()
    if row is None:
        return Cursor(value="", observed_at=None)
    return Cursor(value=row.last_event_id or "", observed_at=row.last_event_at)


async def _read_known_student_ids(
    session: AsyncSession, lea_id: LeaId
) -> set[StudentId]:
    """Return live student IDs in canonical for this LEA.

    Soft-deleted students are still included so enrollments can reference
    them (the schema's snapshot history preserves the deletion).
    """

    rows = (
        await session.execute(
            text("SELECT id FROM students WHERE lea_id = :lea_id"),
            {"lea_id": lea_id},
        )
    ).all()
    return {StudentId(r.id) for r in rows}


async def _read_live_student_count(
    session: AsyncSession, lea_id: LeaId
) -> int:
    """Count canonical students that are not soft-deleted.

    Feeds Layer 5's population-shift check: the live count is the
    denominator the threshold compares the projected post-page count
    against.
    """

    row = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) AS n FROM students
                WHERE lea_id = :lea_id AND deleted_at IS NULL
                """
            ),
            {"lea_id": lea_id},
        )
    ).one()
    return int(row.n)


async def _read_recent_sync_history(
    session: AsyncSession,
    lea_id: LeaId,
    partner: str,
    limit: int = 30,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Return event-count and deletion-count history for Layer 5.

    Pulls the last ``limit`` successful sync_jobs for the (lea, partner)
    pair and computes the per-job deletion count from snapshot rows
    that share the sync_job_id and were written with
    ``deleted_upstream = true``. Returns the two tuples ordered newest
    first; Layer 5 takes the median, so order does not matter, but
    "newest first" is the convention the rest of the codebase uses.
    """

    rows = (
        await session.execute(
            text(
                """
                SELECT id, event_count
                FROM sync_jobs
                WHERE lea_id = :lea_id AND partner = :partner
                  AND status = 'success'
                ORDER BY started_at DESC
                LIMIT :limit
                """
            ),
            {"lea_id": lea_id, "partner": partner, "limit": limit},
        )
    ).all()

    if not rows:
        return ((), ())

    job_ids = [r.id for r in rows]
    event_counts = tuple(int(r.event_count) for r in rows)

    deletion_rows = (
        await session.execute(
            text(
                """
                SELECT generation_id, COUNT(*) AS n
                FROM (
                    SELECT generation_id FROM student_snapshots
                    WHERE generation_id = ANY(:job_ids)
                      AND deleted_upstream
                    UNION ALL
                    SELECT generation_id FROM enrollment_snapshots
                    WHERE generation_id = ANY(:job_ids)
                      AND deleted_upstream
                    UNION ALL
                    SELECT generation_id FROM lea_snapshots
                    WHERE generation_id = ANY(:job_ids)
                      AND deleted_upstream
                ) AS deletions
                GROUP BY generation_id
                """
            ),
            {"job_ids": job_ids},
        )
    ).all()
    deletions_by_job: dict[Any, int] = {
        d.generation_id: int(d.n) for d in deletion_rows
    }
    deletion_counts = tuple(deletions_by_job.get(r.id, 0) for r in rows)

    return event_counts, deletion_counts


async def _latest_live_source_event_id(
    session: AsyncSession,
    lea_id: LeaId,
    entity_type: EntityType,
    natural_key: str,
) -> str | None:
    """Highest source_event_id among live (non-superseded) snapshots.

    Returned value is the dedup high-water mark: events with a
    source_event_id at or below this value have already been written and
    must not produce a new snapshot.
    """

    table = _snapshot_table(entity_type)
    key_col = _natural_key_column(entity_type)
    row = (
        await session.execute(
            text(
                f"""
                SELECT source_event_id
                FROM {table}
                WHERE lea_id = :lea_id AND {key_col} = :nk
                  AND superseded_by_generation_id IS NULL
                ORDER BY source_event_id DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"lea_id": lea_id, "nk": natural_key},
        )
    ).first()
    if row is None:
        return None
    value = row.source_event_id
    return None if value is None else str(value)


# ── Write helpers ─────────────────────────────────────────────────────────────


async def _insert_sync_job_running(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    partner: str,
    cursor_before: str,
    started_at: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO sync_jobs (
                id, lea_id, partner, status, started_at, cursor_before
            ) VALUES (
                :id, :lea_id, :partner, 'running', :started_at, :cursor_before
            )
            """
        ),
        {
            "id": sync_job_id,
            "lea_id": lea_id,
            "partner": partner,
            "started_at": started_at,
            "cursor_before": cursor_before,
        },
    )


async def _persist_validation_issues(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    report: ValidationReport,
    created_at: datetime,
) -> None:
    if not report.issues:
        return
    rows = [
        {
            "sync_job_id": sync_job_id,
            "layer": i.layer,
            "code": i.code,
            "payload_reference": i.event_id,
            "detail": _json_dumps(
                {**i.detail, "severity": i.severity.value}
            ),
            "created_at": created_at,
        }
        for i in report.issues
    ]
    await session.execute(
        text(
            """
            INSERT INTO sync_validation_results (
                sync_job_id, layer, code, payload_reference, detail, created_at
            ) VALUES (
                :sync_job_id, :layer, :code, :payload_reference,
                CAST(:detail AS JSONB), :created_at
            )
            """
        ),
        rows,
    )


async def _mark_sync_job_failed(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    completed_at: datetime,
    error_count: int,
    warning_count: int,
    error_summary: str,
) -> None:
    await session.execute(
        text(
            """
            UPDATE sync_jobs
            SET status = 'failed',
                completed_at = :completed_at,
                error_count = :error_count,
                warning_count = :warning_count,
                error_summary = :error_summary
            WHERE id = :id
            """
        ),
        {
            "id": sync_job_id,
            "completed_at": completed_at,
            "error_count": error_count,
            "warning_count": warning_count,
            "error_summary": error_summary,
        },
    )


async def _mark_sync_job_success(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    completed_at: datetime,
    event_count: int,
    error_count: int,
    warning_count: int,
    cursor_after: str,
) -> None:
    await session.execute(
        text(
            """
            UPDATE sync_jobs
            SET status = 'success',
                completed_at = :completed_at,
                event_count = :event_count,
                error_count = :error_count,
                warning_count = :warning_count,
                cursor_after = :cursor_after
            WHERE id = :id
            """
        ),
        {
            "id": sync_job_id,
            "completed_at": completed_at,
            "event_count": event_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "cursor_after": cursor_after,
        },
    )


async def _upsert_cursor_state(
    *,
    session: AsyncSession,
    lea_id: LeaId,
    partner: str,
    cursor: Cursor,
    last_poll_at: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO cursor_state (
                lea_id, partner, last_event_id, last_event_at,
                last_poll_at, cold_start_required, updated_at
            ) VALUES (
                :lea_id, :partner, :last_event_id, :last_event_at,
                :last_poll_at, false, :updated_at
            )
            ON CONFLICT (lea_id, partner) DO UPDATE SET
                last_event_id = EXCLUDED.last_event_id,
                last_event_at = EXCLUDED.last_event_at,
                last_poll_at = EXCLUDED.last_poll_at,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "lea_id": lea_id,
            "partner": partner,
            "last_event_id": cursor.value or None,
            "last_event_at": cursor.observed_at,
            "last_poll_at": last_poll_at,
            "updated_at": last_poll_at,
        },
    )


async def _write_quarantine_row(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    event: NormalizedEvent,
    created_at: datetime,
) -> None:
    payload = _entity_payload(event)
    await session.execute(
        text(
            """
            INSERT INTO quarantine (
                sync_job_id, lea_id, entity_type, entity_id, reason,
                raw_payload, created_at
            ) VALUES (
                :sync_job_id, :lea_id, :entity_type, :entity_id, :reason,
                CAST(:raw_payload AS JSONB), :created_at
            )
            """
        ),
        {
            "sync_job_id": sync_job_id,
            "lea_id": lea_id,
            "entity_type": event.entity_type.value,
            "entity_id": _natural_key(event),
            "reason": "Layer 4: referential dependency unresolved",
            "raw_payload": _json_dumps(payload | {"source_event_id": event.source_event_id}),
            "created_at": created_at,
        },
    )


async def _ensure_lea_exists(session: AsyncSession, lea_id: LeaId) -> None:
    """Insert a placeholder LEA row if missing.

    Production has a richer onboarding workflow that captures name,
    state, lea_type. The POC keeps the sync worker self-sufficient so the
    demo and tests do not need to bootstrap LEAs out of band; the
    placeholder is overwritten if a real LEA event arrives.
    """

    await session.execute(
        text(
            """
            INSERT INTO leas (id, name, lea_type, state)
            VALUES (:id, :name, :lea_type, :state)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": lea_id,
            "name": f"LEA {lea_id}",
            "lea_type": LeaType.TRADITIONAL_DISTRICT.value,
            "state": "XX",
        },
    )


async def _apply_event(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    event: NormalizedEvent,
    created_at: datetime,
) -> bool:
    """Write snapshot + canonical for one event. Return False if deduped."""

    natural_key = _natural_key(event)
    latest = await _latest_live_source_event_id(
        session, lea_id, event.entity_type, natural_key
    )
    if latest is not None and event.source_event_id <= latest:
        return False

    deleted_upstream = event.operation == Operation.DELETED
    await _mark_prior_snapshot_superseded(
        session=session,
        sync_job_id=sync_job_id,
        lea_id=lea_id,
        entity_type=event.entity_type,
        natural_key=natural_key,
        superseded_at=created_at,
    )
    await _insert_snapshot(
        session=session,
        sync_job_id=sync_job_id,
        lea_id=lea_id,
        event=event,
        deleted_upstream=deleted_upstream,
        created_at=created_at,
    )
    await _upsert_canonical(
        session=session,
        lea_id=lea_id,
        event=event,
        deleted_upstream=deleted_upstream,
        now=created_at,
    )
    return True


async def _mark_prior_snapshot_superseded(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    entity_type: EntityType,
    natural_key: str,
    superseded_at: datetime,
) -> None:
    table = _snapshot_table(entity_type)
    key_col = _natural_key_column(entity_type)
    await session.execute(
        text(
            f"""
            UPDATE {table}
            SET superseded_by_generation_id = :gen_id,
                superseded_at = :superseded_at
            WHERE lea_id = :lea_id AND {key_col} = :nk
              AND superseded_by_generation_id IS NULL
            """
        ),
        {
            "gen_id": sync_job_id,
            "superseded_at": superseded_at,
            "lea_id": lea_id,
            "nk": natural_key,
        },
    )


async def _insert_snapshot(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    event: NormalizedEvent,
    deleted_upstream: bool,
    created_at: datetime,
) -> None:
    table = _snapshot_table(event.entity_type)
    key_col = _natural_key_column(event.entity_type)
    payload = _entity_payload(event)

    if event.entity_type == EntityType.LEA:
        # LEA snapshots use lea_id as the natural key; no extra column.
        sql = f"""
            INSERT INTO {table} (
                {key_col}, generation_id, deleted_upstream,
                source_event_id, source_event_at, created_at, payload
            ) VALUES (
                :nk, :gen_id, :deleted_upstream,
                :source_event_id, :source_event_at, :created_at,
                CAST(:payload AS JSONB)
            )
        """
        params: dict[str, Any] = {
            "nk": _natural_key(event),
            "gen_id": sync_job_id,
            "deleted_upstream": deleted_upstream,
            "source_event_id": event.source_event_id,
            "source_event_at": event.occurred_at,
            "created_at": created_at,
            "payload": _json_dumps(payload),
        }
    else:
        sql = f"""
            INSERT INTO {table} (
                {key_col}, lea_id, generation_id, deleted_upstream,
                source_event_id, source_event_at, created_at, payload
            ) VALUES (
                :nk, :lea_id, :gen_id, :deleted_upstream,
                :source_event_id, :source_event_at, :created_at,
                CAST(:payload AS JSONB)
            )
        """
        params = {
            "nk": _natural_key(event),
            "lea_id": lea_id,
            "gen_id": sync_job_id,
            "deleted_upstream": deleted_upstream,
            "source_event_id": event.source_event_id,
            "source_event_at": event.occurred_at,
            "created_at": created_at,
            "payload": _json_dumps(payload),
        }
    await session.execute(text(sql), params)


async def _upsert_canonical(
    *,
    session: AsyncSession,
    lea_id: LeaId,
    event: NormalizedEvent,
    deleted_upstream: bool,
    now: datetime,
) -> None:
    if event.entity_type == EntityType.STUDENT:
        await _upsert_student(session, event, deleted_upstream, now)
    elif event.entity_type == EntityType.ENROLLMENT:
        await _upsert_enrollment(session, event, deleted_upstream, now)
    elif event.entity_type == EntityType.LEA:
        await _upsert_lea_entity(session, event, deleted_upstream, now)


async def _upsert_student(
    session: AsyncSession,
    event: NormalizedEvent,
    deleted_upstream: bool,
    now: datetime,
) -> None:
    entity = event.entity
    assert isinstance(entity, Student)
    await session.execute(
        text(
            """
            INSERT INTO students (
                id, lea_id, given_name, family_name, grade,
                preferred_first_name, primary_school_id, external_ids,
                deleted_at
            ) VALUES (
                :id, :lea_id, :given_name, :family_name, :grade,
                :preferred_first_name, :primary_school_id,
                CAST(:external_ids AS JSONB), :deleted_at
            )
            ON CONFLICT (id) DO UPDATE SET
                lea_id = EXCLUDED.lea_id,
                given_name = EXCLUDED.given_name,
                family_name = EXCLUDED.family_name,
                grade = EXCLUDED.grade,
                preferred_first_name = EXCLUDED.preferred_first_name,
                primary_school_id = EXCLUDED.primary_school_id,
                external_ids = EXCLUDED.external_ids,
                deleted_at = EXCLUDED.deleted_at
            """
        ),
        {
            "id": entity.id,
            "lea_id": entity.lea_id,
            "given_name": entity.given_name,
            "family_name": entity.family_name,
            "grade": entity.grade,
            "preferred_first_name": entity.preferred_first_name,
            "primary_school_id": entity.primary_school_id,
            "external_ids": _json_dumps(entity.external_ids),
            "deleted_at": now if deleted_upstream else None,
        },
    )


async def _upsert_enrollment(
    session: AsyncSession,
    event: NormalizedEvent,
    deleted_upstream: bool,
    now: datetime,
) -> None:
    entity = event.entity
    assert isinstance(entity, Enrollment)
    await session.execute(
        text(
            """
            INSERT INTO enrollments (
                id, lea_id, student_id, class_id, begin_date, end_date,
                deleted_at
            ) VALUES (
                :id, :lea_id, :student_id, :class_id, :begin_date,
                :end_date, :deleted_at
            )
            ON CONFLICT (id) DO UPDATE SET
                lea_id = EXCLUDED.lea_id,
                student_id = EXCLUDED.student_id,
                class_id = EXCLUDED.class_id,
                begin_date = EXCLUDED.begin_date,
                end_date = EXCLUDED.end_date,
                deleted_at = EXCLUDED.deleted_at
            """
        ),
        {
            "id": entity.id,
            "lea_id": entity.lea_id,
            "student_id": entity.student_id,
            "class_id": entity.class_id,
            "begin_date": entity.begin_date,
            "end_date": entity.end_date,
            "deleted_at": now if deleted_upstream else None,
        },
    )


async def _upsert_lea_entity(
    session: AsyncSession,
    event: NormalizedEvent,
    deleted_upstream: bool,
    now: datetime,
) -> None:
    entity = event.entity
    assert isinstance(entity, Lea)
    await session.execute(
        text(
            """
            INSERT INTO leas (id, name, lea_type, state, nces_lea_id, deleted_at)
            VALUES (:id, :name, :lea_type, :state, :nces_lea_id, :deleted_at)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                lea_type = EXCLUDED.lea_type,
                state = EXCLUDED.state,
                nces_lea_id = EXCLUDED.nces_lea_id,
                deleted_at = EXCLUDED.deleted_at
            """
        ),
        {
            "id": entity.id,
            "name": entity.name,
            "lea_type": entity.lea_type.value,
            "state": entity.state,
            "nces_lea_id": entity.nces_lea_id,
            "deleted_at": now if deleted_upstream else None,
        },
    )


# ── Helpers (pure) ────────────────────────────────────────────────────────────


_SNAPSHOT_TABLE_BY_ENTITY: dict[EntityType, str] = {
    EntityType.LEA: "lea_snapshots",
    EntityType.STUDENT: "student_snapshots",
    EntityType.ENROLLMENT: "enrollment_snapshots",
}

_NATURAL_KEY_COLUMN_BY_ENTITY: dict[EntityType, str] = {
    EntityType.LEA: "lea_id",
    EntityType.STUDENT: "student_id",
    EntityType.ENROLLMENT: "enrollment_id",
}


def _snapshot_table(entity_type: EntityType) -> str:
    return _SNAPSHOT_TABLE_BY_ENTITY[entity_type]


def _natural_key_column(entity_type: EntityType) -> str:
    return _NATURAL_KEY_COLUMN_BY_ENTITY[entity_type]


def _natural_key(event: NormalizedEvent) -> str:
    entity = event.entity
    return str(getattr(entity, "id"))


def _entity_payload(event: NormalizedEvent) -> dict[str, Any]:
    """Return a JSON-safe dict for the canonical entity.

    ``dataclasses.asdict`` does the structural work; we only need to walk
    the result and convert non-JSON types (``date``, ``Enum``) to their
    serialized form. Snapshot ``payload`` is the only durable record of
    the entity's state at write time, so it has to round-trip.
    """

    raw: dict[str, Any]
    if dataclasses.is_dataclass(event.entity) and not isinstance(
        event.entity, type
    ):
        raw = asdict(event.entity)
    else:
        raw = {}
    safe = _json_safe(raw)
    assert isinstance(safe, dict)
    return safe


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(_json_safe(value))


def _summarize_blocking_errors(report: ValidationReport) -> str:
    """Build a short error_summary string for sync_jobs.

    Only includes page-level errors and the first two per-event errors so
    the column stays human-readable. The full set is in
    sync_validation_results.
    """

    blocking = [
        i for i in report.issues if i.severity == Severity.ERROR
    ]
    parts: list[str] = []
    for issue in blocking[:3]:
        if issue.event_id is None:
            parts.append(f"L{issue.layer}:{issue.code}")
        else:
            parts.append(f"L{issue.layer}:{issue.code}@{issue.event_id}")
    if len(blocking) > 3:
        parts.append(f"(+{len(blocking) - 3} more)")
    return "; ".join(parts) if parts else "page blocked"


# Public re-export for tests that want CanonicalEntity in type annotations.
__all__ = ["PageOutcome", "SyncWorker", "CanonicalEntity"]
