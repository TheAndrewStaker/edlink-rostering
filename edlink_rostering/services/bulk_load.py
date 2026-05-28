"""Bulk-load cold-start path.

Per ``docs/design/edlink-oneroster-rostering.md`` § "Data flow:
bulk-load happy path." Triggered when:

1. The operator explicitly invokes bulk-load (e.g., from the admin
   dashboard for an LEA that has just been onboarded).
2. The poll worker observes ``cold_start_required = true`` on the
   cursor state row (i.e., the per-LEA cursor has fallen past the
   30-day Events API retention window and incremental replay is no
   longer possible).

The bulk-load walks the partner's resource endpoints in dependency
order (orgs → academic_sessions → students → classes → enrollments)
and writes the resulting canonical state idempotently. After all
pages complete, it fetches the latest event id from the partner's
events endpoint and sets the cursor so subsequent incremental polls
resume against current state. Finally it clears
``cold_start_required``.

Two injected seams keep this module connector-agnostic:

- ``partner_snapshot`` returns the projected partner-side state per
  entity type. In production this walks
  ``GET /api/v1.0/graph/people``, ``/classes``, etc. In the POC the
  EdLink client provides a fixture-projected version.
- ``latest_cursor_provider`` returns the current latest cursor from
  the partner. In production this hits
  ``GET /api/v1.0/graph/events?limit=1``; in the POC the EdLink
  client returns the last event from the fixture.

Idempotency: bulk-load inserts a new snapshot row only when the
entity content has changed from the live snapshot for the same
natural key. A second bulk-load against unchanged partner state
writes zero new snapshot rows but still advances the cursor (the
partner may have moved forward between bulk-loads). The cursor
advance is the load-bearing piece that prevents the cold-start path
from looping.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import Cursor, LeaId


# Resource-walk shape. Tests and the EdLink client both supply a
# `{"students": [...], "enrollments": [...]}` dict shaped to the
# Postgres column subset the upsert uses.
PartnerSnapshot = Callable[
    [LeaId], Awaitable[dict[str, list[dict[str, Any]]]]
]

LatestCursorProvider = Callable[[LeaId], Awaitable[Cursor]]


# Per-entity-type configuration. Maps the partner-payload key to the
# canonical table, the snapshot table, the natural key column, and the
# subset of columns the upsert + snapshot writes care about.
_ENTITY_PLAN: dict[
    str,
    tuple[str, str, str, tuple[str, ...]],
] = {
    "students": (
        "students",
        "student_snapshots",
        "student_id",
        (
            "id",
            "lea_id",
            "given_name",
            "family_name",
            "grade",
            "preferred_first_name",
            "primary_school_id",
        ),
    ),
    "enrollments": (
        "enrollments",
        "enrollment_snapshots",
        "enrollment_id",
        (
            "id",
            "lea_id",
            "student_id",
            "class_id",
            "begin_date",
            "end_date",
        ),
    ),
}

# Dependency order: parents before children. The bulk-load processes
# students before enrollments so the FK from enrollments.student_id
# resolves at insert time.
_DEPENDENCY_ORDER = ("students", "enrollments")


@dataclass(frozen=True)
class BulkLoadReport:
    """Outcome of one bulk-load run."""

    sync_job_id: uuid.UUID
    lea_id: LeaId
    partner: str
    started_at: datetime
    completed_at: datetime
    status: str
    rows_per_entity_type: dict[str, int]
    snapshots_written: dict[str, int]
    cursor_after: str
    cold_start_cleared: bool


class BulkLoadError(Exception):
    """Base class for bulk-load failures."""


class BulkLoadService:
    """Walks a partner snapshot into canonical state in one cold-start pass."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = session_factory

    async def bulk_load_lea(
        self,
        *,
        lea_id: LeaId,
        partner: str,
        partner_snapshot: PartnerSnapshot,
        latest_cursor_provider: LatestCursorProvider,
    ) -> BulkLoadReport:
        """Execute one bulk-load pass for ``(lea_id, partner)``.

        Returns a report describing how many rows were processed per
        entity type, how many new snapshots were written, and the
        cursor the LEA is now positioned at.
        """

        started_at = datetime.now(UTC)
        sync_job_id = uuid.uuid4()

        await self._insert_sync_job_running(
            sync_job_id=sync_job_id,
            lea_id=lea_id,
            partner=partner,
            started_at=started_at,
        )

        rows_per_type: dict[str, int] = {}
        snapshots_written: dict[str, int] = {}
        try:
            payload = await partner_snapshot(lea_id)
            await self._ensure_lea(lea_id)
            for entity_name in _DEPENDENCY_ORDER:
                entries = payload.get(entity_name, [])
                rows_per_type[entity_name] = len(entries)
                written = await self._apply_entity_type(
                    sync_job_id=sync_job_id,
                    lea_id=lea_id,
                    entity_name=entity_name,
                    entries=entries,
                    created_at=datetime.now(UTC),
                )
                snapshots_written[entity_name] = written

            latest = await latest_cursor_provider(lea_id)
            await self._advance_cursor(
                lea_id=lea_id,
                partner=partner,
                cursor=latest,
                now=datetime.now(UTC),
            )
            cold_start_cleared = True
            completed_at = datetime.now(UTC)
            await self._mark_sync_job_success(
                sync_job_id=sync_job_id,
                completed_at=completed_at,
                cursor_after=latest.value,
                event_count=sum(snapshots_written.values()),
            )
        except Exception as exc:
            completed_at = datetime.now(UTC)
            await self._mark_sync_job_failed(
                sync_job_id=sync_job_id,
                completed_at=completed_at,
                error_summary=f"bulk_load_failed: {exc}",
            )
            raise BulkLoadError(str(exc)) from exc

        return BulkLoadReport(
            sync_job_id=sync_job_id,
            lea_id=lea_id,
            partner=partner,
            started_at=started_at,
            completed_at=completed_at,
            status="success",
            rows_per_entity_type=rows_per_type,
            snapshots_written=snapshots_written,
            cursor_after=latest.value,
            cold_start_cleared=cold_start_cleared,
        )

    # ── Per-entity-type application ───────────────────────────────────────

    async def _apply_entity_type(
        self,
        *,
        sync_job_id: uuid.UUID,
        lea_id: LeaId,
        entity_name: str,
        entries: list[dict[str, Any]],
        created_at: datetime,
    ) -> int:
        """Write canonical + snapshot rows for one entity type.

        Returns the number of new snapshot rows written. Entries whose
        current snapshot already matches the bulk-load content are
        skipped, so a re-run against unchanged data writes zero new
        rows.
        """

        canonical_table, snapshot_table, key_col, columns = _ENTITY_PLAN[
            entity_name
        ]
        written = 0
        async with self._sessions() as session:
            for entry in entries:
                row = {col: entry.get(col) for col in columns}
                if row.get("id") is None:
                    raise BulkLoadError(
                        f"Bulk-load entry for {entity_name} missing 'id'."
                    )
                row["lea_id"] = lea_id
                natural_key = str(row["id"])

                current = await self._read_current_snapshot_payload(
                    session=session,
                    snapshot_table=snapshot_table,
                    key_col=key_col,
                    natural_key=natural_key,
                    lea_id=lea_id,
                )
                desired_payload = _canonical_payload(row)
                if current is not None and current == desired_payload:
                    continue

                await self._supersede_prior_snapshot(
                    session=session,
                    snapshot_table=snapshot_table,
                    key_col=key_col,
                    natural_key=natural_key,
                    lea_id=lea_id,
                    sync_job_id=sync_job_id,
                    superseded_at=created_at,
                )
                await self._insert_snapshot(
                    session=session,
                    snapshot_table=snapshot_table,
                    key_col=key_col,
                    natural_key=natural_key,
                    lea_id=lea_id,
                    sync_job_id=sync_job_id,
                    payload=desired_payload,
                    created_at=created_at,
                )
                await self._upsert_canonical_row(
                    session=session,
                    canonical_table=canonical_table,
                    row=row,
                )
                written += 1
            await session.commit()
        return written

    # ── Helpers: snapshot read + write ────────────────────────────────────

    async def _read_current_snapshot_payload(
        self,
        *,
        session: AsyncSession,
        snapshot_table: str,
        key_col: str,
        natural_key: str,
        lea_id: LeaId,
    ) -> dict[str, Any] | None:
        row = (
            await session.execute(
                text(
                    f"""
                    SELECT payload FROM {snapshot_table}
                    WHERE {key_col} = :nk AND lea_id = :lea
                      AND superseded_by_generation_id IS NULL
                    """
                ),
                {"nk": natural_key, "lea": lea_id},
            )
        ).first()
        if row is None:
            return None
        return row.payload  # type: ignore[no-any-return]

    async def _supersede_prior_snapshot(
        self,
        *,
        session: AsyncSession,
        snapshot_table: str,
        key_col: str,
        natural_key: str,
        lea_id: LeaId,
        sync_job_id: uuid.UUID,
        superseded_at: datetime,
    ) -> None:
        await session.execute(
            text(
                f"""
                UPDATE {snapshot_table}
                SET superseded_by_generation_id = :gen,
                    superseded_at = :now
                WHERE {key_col} = :nk AND lea_id = :lea
                  AND superseded_by_generation_id IS NULL
                """
            ),
            {
                "gen": sync_job_id,
                "now": superseded_at,
                "nk": natural_key,
                "lea": lea_id,
            },
        )

    async def _insert_snapshot(
        self,
        *,
        session: AsyncSession,
        snapshot_table: str,
        key_col: str,
        natural_key: str,
        lea_id: LeaId,
        sync_job_id: uuid.UUID,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        source_event_id = f"bulk-load-{sync_job_id}"
        await session.execute(
            text(
                f"""
                INSERT INTO {snapshot_table} (
                    {key_col}, lea_id, generation_id, deleted_upstream,
                    source_event_id, source_event_at, created_at, payload
                ) VALUES (
                    :nk, :lea, :gen, false,
                    :sev, :sev_at, :now,
                    CAST(:payload AS JSONB)
                )
                """
            ),
            {
                "nk": natural_key,
                "lea": lea_id,
                "gen": sync_job_id,
                "sev": source_event_id,
                "sev_at": created_at,
                "now": created_at,
                "payload": json.dumps(payload, default=_json_default),
            },
        )

    async def _upsert_canonical_row(
        self,
        *,
        session: AsyncSession,
        canonical_table: str,
        row: dict[str, Any],
    ) -> None:
        if canonical_table == "students":
            await session.execute(
                text(
                    """
                    INSERT INTO students (
                        id, lea_id, given_name, family_name, grade,
                        preferred_first_name, primary_school_id, external_ids
                    ) VALUES (
                        :id, :lea_id, :given_name, :family_name, :grade,
                        :preferred_first_name, :primary_school_id,
                        CAST('{}' AS JSONB)
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        lea_id = EXCLUDED.lea_id,
                        given_name = EXCLUDED.given_name,
                        family_name = EXCLUDED.family_name,
                        grade = EXCLUDED.grade,
                        preferred_first_name = EXCLUDED.preferred_first_name,
                        primary_school_id = EXCLUDED.primary_school_id,
                        deleted_at = NULL
                    """
                ),
                row,
            )
        elif canonical_table == "enrollments":
            await session.execute(
                text(
                    """
                    INSERT INTO enrollments (
                        id, lea_id, student_id, class_id, begin_date, end_date
                    ) VALUES (
                        :id, :lea_id, :student_id, :class_id,
                        :begin_date, :end_date
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        lea_id = EXCLUDED.lea_id,
                        student_id = EXCLUDED.student_id,
                        class_id = EXCLUDED.class_id,
                        begin_date = EXCLUDED.begin_date,
                        end_date = EXCLUDED.end_date,
                        deleted_at = NULL
                    """
                ),
                row,
            )
        else:
            raise BulkLoadError(
                f"No upsert plan for canonical_table={canonical_table!r}."
            )

    # ── Sync job lifecycle + cursor advance + LEA bootstrap ───────────────

    async def _ensure_lea(self, lea_id: LeaId) -> None:
        """Insert a placeholder LEA row so the FK from canonical resolves.

        Mirrors the sync_worker's `_ensure_lea_exists` helper; bulk-load
        can be invoked for an LEA whose row was never created in
        production if the onboarding flow has not run yet.
        """

        async with self._sessions() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO leas (id, name, lea_type, state)
                    VALUES (:id, :name, 'traditional_district', 'XX')
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": lea_id, "name": f"LEA {lea_id}"},
            )
            await session.commit()

    async def _insert_sync_job_running(
        self,
        *,
        sync_job_id: uuid.UUID,
        lea_id: LeaId,
        partner: str,
        started_at: datetime,
    ) -> None:
        async with self._sessions() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO sync_jobs (
                        id, lea_id, partner, status, started_at, cursor_before
                    ) VALUES (
                        :id, :lea, :partner, 'bulk_load', :now, ''
                    )
                    """
                ),
                {
                    "id": sync_job_id,
                    "lea": lea_id,
                    "partner": partner,
                    "now": started_at,
                },
            )
            await session.commit()

    async def _mark_sync_job_success(
        self,
        *,
        sync_job_id: uuid.UUID,
        completed_at: datetime,
        cursor_after: str,
        event_count: int,
    ) -> None:
        async with self._sessions() as session:
            await session.execute(
                text(
                    """
                    UPDATE sync_jobs
                    SET status = 'success',
                        completed_at = :now,
                        event_count = :n,
                        cursor_after = :cursor
                    WHERE id = :id
                    """
                ),
                {
                    "id": sync_job_id,
                    "now": completed_at,
                    "n": event_count,
                    "cursor": cursor_after,
                },
            )
            await session.commit()

    async def _mark_sync_job_failed(
        self,
        *,
        sync_job_id: uuid.UUID,
        completed_at: datetime,
        error_summary: str,
    ) -> None:
        async with self._sessions() as session:
            await session.execute(
                text(
                    """
                    UPDATE sync_jobs
                    SET status = 'failed',
                        completed_at = :now,
                        error_summary = :err
                    WHERE id = :id
                    """
                ),
                {
                    "id": sync_job_id,
                    "now": completed_at,
                    "err": error_summary,
                },
            )
            await session.commit()

    async def _advance_cursor(
        self,
        *,
        lea_id: LeaId,
        partner: str,
        cursor: Cursor,
        now: datetime,
    ) -> None:
        """Set the cursor and clear ``cold_start_required``.

        The bulk-load is what unwedges an LEA whose cursor fell past
        the 30-day retention window; this update is the load-bearing
        side effect that lets incremental polls resume.
        """

        async with self._sessions() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO cursor_state (
                        lea_id, partner, last_event_id, last_event_at,
                        last_poll_at, cold_start_required, updated_at
                    ) VALUES (
                        :lea, :partner, :last_event_id, :last_event_at,
                        :now, false, :now
                    )
                    ON CONFLICT (lea_id, partner) DO UPDATE SET
                        last_event_id = EXCLUDED.last_event_id,
                        last_event_at = EXCLUDED.last_event_at,
                        last_poll_at = EXCLUDED.last_poll_at,
                        cold_start_required = false,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "lea": lea_id,
                    "partner": partner,
                    "last_event_id": cursor.value or None,
                    "last_event_at": cursor.observed_at,
                    "now": now,
                },
            )
            await session.commit()


# ── Pure helpers ──────────────────────────────────────────────────────────────


def _canonical_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Project a row dict into a JSON-safe canonical payload.

    Round-trips through json.dumps with ``_json_default`` so the
    "current snapshot matches" comparison reads back from JSONB
    consistently.
    """

    payload: dict[str, Any] = json.loads(
        json.dumps(row, default=_json_default)
    )
    return payload


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


__all__ = [
    "BulkLoadError",
    "BulkLoadReport",
    "BulkLoadService",
    "LatestCursorProvider",
    "PartnerSnapshot",
]
