"""Quarantine review service.

Three operator actions for unresolved quarantine rows:

1. **list_unresolved**: read-only listing for an LEA. Powers
   ``edlink-rostering list-quarantine`` and the admin app.
2. **release**: attempt to re-validate the row's referential constraints
   against current canonical state. If the missing target now exists
   (typical case: the enrollment's student arrived in a later sync),
   write a synthetic sync_jobs row of ``status='quarantine_release'``,
   write a snapshot, upsert canonical, mark the quarantine row resolved.
   If the target still does not exist, raise :class:`QuarantineRefused`
   so the operator can decide between waiting or rejecting.
3. **reject**: mark the quarantine row resolved with status='rejected'
   and free-text reason. No canonical change.

Both release and reject are idempotent on the resolution columns: a
second call on an already-resolved row raises
:class:`QuarantineAlreadyResolved` so the operator's intent stays
explicit.

For the POC the release path only handles enrollment orphans (the only
shape Layer 4 quarantines today). Adding a new quarantine-able entity
shape extends ``_apply_released_event`` rather than the action surface.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId


@dataclass(frozen=True)
class QuarantineRow:
    """Read-side row shape for ``list-quarantine`` callers."""

    id: uuid.UUID
    sync_job_id: uuid.UUID
    lea_id: LeaId
    entity_type: str
    entity_id: str
    reason: str
    created_at: datetime
    resolved_at: datetime | None
    resolution_status: str | None
    resolution_operator: str | None


@dataclass(frozen=True)
class ReleaseOutcome:
    quarantine_id: uuid.UUID
    sync_job_id: uuid.UUID
    release_generation_id: uuid.UUID
    entity_type: str
    entity_id: str


@dataclass(frozen=True)
class RejectOutcome:
    quarantine_id: uuid.UUID
    rejected_at: datetime
    reason: str


class QuarantineError(RuntimeError):
    """Base for quarantine action failures."""


class QuarantineNotFound(QuarantineError):
    """The named quarantine row does not exist."""


class QuarantineAlreadyResolved(QuarantineError):
    """The quarantine row was previously resolved (released or
    rejected). The operator must inspect history before re-acting."""


class QuarantineRefused(QuarantineError):
    """The release re-validation failed (FK still unresolved).
    Operator should wait for the upstream fix or reject the row."""


class QuarantineService:
    """Operator-driven quarantine review."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = session_factory

    async def list_unresolved(
        self,
        *,
        lea_id: LeaId | None = None,
        authorized_leas: frozenset[LeaId] | None = None,
        limit: int = 50,
    ) -> list[QuarantineRow]:
        """Unresolved quarantine rows, oldest first.

        ``lea_id`` narrows to one LEA (request-supplied filter; any
        role may use it). ``authorized_leas`` is the operator-role
        scope filter and composes with ``lea_id``; passing ``None`` is
        "no scope filter" (owner, admin, auditor see
        every LEA). Same shape as
        :mod:`edlink_rostering.services.queries.cursors`.
        """

        sql = (
            "SELECT id, sync_job_id, lea_id, entity_type,"
            " entity_id, reason, created_at,"
            " resolved_at, resolution_status,"
            " resolution_operator"
            " FROM quarantine WHERE resolved_at IS NULL"
        )
        params: dict[str, Any] = {"limit": limit}
        if lea_id is not None:
            sql += " AND lea_id = :lea"
            params["lea"] = lea_id
        if authorized_leas is not None:
            sql += " AND lea_id = ANY(:leas)"
            params["leas"] = list(authorized_leas)
        sql += " ORDER BY created_at ASC LIMIT :limit"

        async with self._sessions() as session:
            rows = (await session.execute(text(sql), params)).all()
        return [
            QuarantineRow(
                id=r.id,
                sync_job_id=r.sync_job_id,
                lea_id=LeaId(r.lea_id),
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                reason=r.reason,
                created_at=r.created_at,
                resolved_at=r.resolved_at,
                resolution_status=r.resolution_status,
                resolution_operator=r.resolution_operator,
            )
            for r in rows
        ]

    async def release(
        self,
        *,
        quarantine_id: uuid.UUID,
        operator_identity: str,
    ) -> ReleaseOutcome:
        """Re-validate the FK; if it resolves, apply the event and
        mark the quarantine row released."""

        async with self._sessions() as session:
            now = datetime.now(UTC)
            # FOR UPDATE on the release path serializes concurrent
            # release attempts: the second arrival blocks on the row
            # lock until the first commits, then re-reads with the
            # already-resolved guard tripped and raises
            # QuarantineAlreadyResolved. Without it, both arrivals
            # would write synthetic sync_jobs rows and double-count
            # the action.
            row = await self._load_unresolved(
                session, quarantine_id, for_update=True
            )

            payload = row.raw_payload
            if not isinstance(payload, dict):
                raise QuarantineRefused(
                    "Quarantine raw_payload is not a JSON object; "
                    "cannot release."
                )

            if row.entity_type != "enrollment":
                raise QuarantineRefused(
                    f"Quarantine release only supports enrollment rows; "
                    f"got entity_type={row.entity_type!r}."
                )

            student_id = payload.get("student_id")
            if not isinstance(student_id, str):
                raise QuarantineRefused(
                    "Enrollment payload is missing 'student_id'."
                )

            target_exists = await self._student_exists(
                session, LeaId(row.lea_id), student_id
            )
            if not target_exists:
                raise QuarantineRefused(
                    f"student_id={student_id!r} still does not exist for "
                    f"lea_id={row.lea_id!r}. Wait for the upstream "
                    "person.created event or reject this quarantine row."
                )

            release_generation_id = uuid.uuid4()
            await self._insert_release_sync_job_row(
                session=session,
                release_generation_id=release_generation_id,
                lea_id=LeaId(row.lea_id),
                started_at=now,
                quarantine_id=quarantine_id,
            )
            await self._insert_enrollment_snapshot(
                session=session,
                release_generation_id=release_generation_id,
                lea_id=LeaId(row.lea_id),
                payload=payload,
                created_at=now,
            )
            await self._upsert_enrollment_canonical(
                session=session,
                lea_id=LeaId(row.lea_id),
                payload=payload,
            )
            await self._mark_resolved(
                session=session,
                quarantine_id=quarantine_id,
                resolved_at=now,
                resolution_status="released",
                resolution_operator=operator_identity,
            )
            await session.commit()

            return ReleaseOutcome(
                quarantine_id=quarantine_id,
                sync_job_id=row.sync_job_id,
                release_generation_id=release_generation_id,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
            )

    async def reject(
        self,
        *,
        quarantine_id: uuid.UUID,
        operator_identity: str,
        reason: str,
    ) -> RejectOutcome:
        async with self._sessions() as session:
            now = datetime.now(UTC)
            row = await self._load_unresolved(session, quarantine_id)
            full_reason = f"rejected: {reason}; original: {row.reason}"
            await self._mark_resolved(
                session=session,
                quarantine_id=quarantine_id,
                resolved_at=now,
                resolution_status="rejected",
                resolution_operator=operator_identity,
                reason_override=full_reason,
            )
            await session.commit()
            return RejectOutcome(
                quarantine_id=quarantine_id,
                rejected_at=now,
                reason=reason,
            )

    async def _load_unresolved(
        self,
        session: AsyncSession,
        quarantine_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Any:
        """Read the quarantine row, optionally with SELECT ... FOR UPDATE.

        ``release()`` passes ``for_update=True`` so a second concurrent
        release on the same row blocks until the first commits and
        then sees the already-resolved guard. ``reject()`` does not
        need the lock because it is idempotent on ``resolved_at`` and
        does not insert a synthetic sync_jobs row that would
        double-count under contention.
        """

        query = (
            "SELECT id, sync_job_id, lea_id, entity_type, "
            "entity_id, reason, raw_payload, resolved_at "
            "FROM quarantine WHERE id = :id"
        )
        if for_update:
            query += " FOR UPDATE"
        row = (
            await session.execute(text(query), {"id": quarantine_id})
        ).first()
        if row is None:
            raise QuarantineNotFound(
                f"quarantine_id {quarantine_id} not found"
            )
        if row.resolved_at is not None:
            raise QuarantineAlreadyResolved(
                f"quarantine_id {quarantine_id} was already resolved "
                "(see resolution_status)."
            )
        return row

    async def _student_exists(
        self,
        session: AsyncSession,
        lea_id: LeaId,
        student_id: str,
    ) -> bool:
        row = (
            await session.execute(
                text(
                    """
                    SELECT 1 FROM students
                    WHERE lea_id = :lea AND id = :sid
                    LIMIT 1
                    """
                ),
                {"lea": lea_id, "sid": student_id},
            )
        ).first()
        return row is not None

    async def _insert_release_sync_job_row(
        self,
        *,
        session: AsyncSession,
        release_generation_id: uuid.UUID,
        lea_id: LeaId,
        started_at: datetime,
        quarantine_id: uuid.UUID,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs (
                    id, lea_id, partner, status, started_at, completed_at,
                    event_count, error_summary
                ) VALUES (
                    :id, :lea_id, 'operator', 'quarantine_release',
                    :started_at, :started_at, 1, :error_summary
                )
                """
            ),
            {
                "id": release_generation_id,
                "lea_id": lea_id,
                "started_at": started_at,
                "error_summary": (
                    f"release of quarantine row {quarantine_id}"
                ),
            },
        )

    async def _insert_enrollment_snapshot(
        self,
        *,
        session: AsyncSession,
        release_generation_id: uuid.UUID,
        lea_id: LeaId,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO enrollment_snapshots (
                    enrollment_id, lea_id, generation_id, deleted_upstream,
                    source_event_id, source_event_at, created_at, payload
                ) VALUES (
                    :nk, :lea_id, :gen_id, false,
                    :source_event_id, :source_event_at, :created_at,
                    CAST(:payload AS JSONB)
                )
                """
            ),
            {
                "nk": payload.get("id"),
                "lea_id": lea_id,
                "gen_id": release_generation_id,
                "source_event_id": payload.get("source_event_id"),
                "source_event_at": created_at,
                "created_at": created_at,
                "payload": json.dumps(payload, default=_json_default),
            },
        )

    async def _upsert_enrollment_canonical(
        self,
        *,
        session: AsyncSession,
        lea_id: LeaId,
        payload: dict[str, Any],
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO enrollments (
                    id, lea_id, student_id, class_id, begin_date, end_date,
                    deleted_at
                ) VALUES (
                    :id, :lea_id, :student_id, :class_id, :begin_date,
                    :end_date, NULL
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
                "id": payload.get("id"),
                "lea_id": lea_id,
                "student_id": payload.get("student_id"),
                "class_id": payload.get("class_id"),
                "begin_date": _parse_iso_date(payload.get("begin_date")),
                "end_date": _parse_iso_date(payload.get("end_date")),
            },
        )

    async def _mark_resolved(
        self,
        *,
        session: AsyncSession,
        quarantine_id: uuid.UUID,
        resolved_at: datetime,
        resolution_status: str,
        resolution_operator: str,
        reason_override: str | None = None,
    ) -> None:
        if reason_override is None:
            await session.execute(
                text(
                    """
                    UPDATE quarantine
                    SET resolved_at = :ra,
                        resolution_status = :rs,
                        resolution_operator = :ro
                    WHERE id = :id
                    """
                ),
                {
                    "id": quarantine_id,
                    "ra": resolved_at,
                    "rs": resolution_status,
                    "ro": resolution_operator,
                },
            )
        else:
            await session.execute(
                text(
                    """
                    UPDATE quarantine
                    SET resolved_at = :ra,
                        resolution_status = :rs,
                        resolution_operator = :ro,
                        reason = :reason
                    WHERE id = :id
                    """
                ),
                {
                    "id": quarantine_id,
                    "ra": resolved_at,
                    "rs": resolution_status,
                    "ro": resolution_operator,
                    "reason": reason_override,
                },
            )


def _parse_iso_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unserializable: {type(value)!r}")


__all__ = [
    "QuarantineAlreadyResolved",
    "QuarantineError",
    "QuarantineNotFound",
    "QuarantineRefused",
    "QuarantineRow",
    "QuarantineService",
    "RejectOutcome",
    "ReleaseOutcome",
]
