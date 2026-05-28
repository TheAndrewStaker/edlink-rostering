"""Soft-delete revert with compensating audit.

The deep cut of the POC. Reverting a sync job undoes its writes without
hard-deleting anything: prior snapshots get their ``superseded_by_*``
fields cleared (becoming live again), the reverted snapshots get tagged
with the revert's generation ID, and canonical rows are rewound to the
prior snapshot's payload (or soft-deleted if no prior snapshot exists).

This matches the Airbyte 1.0+ / Fivetran Pro revert model documented in
``docs/design/edlink-oneroster-rostering.md`` and the ADR-driven decision
in [[project-post-interview-design-pass]] memory: never hard-delete from
canonical or snapshots, always preserve the audit trail.

Invariants the implementation maintains:

1. **Idempotent on double-click.** A second ``revert(sync_job_id)`` after
   the first one finds zero "live" snapshots from the target sync_job
   (because the first revert already moved them under a revert generation
   ID), so it makes no data changes. It still writes a ``revert_actions``
   audit row so the operator timeline reflects every operator action.
2. **Compensating audit is explicit.** Every call writes one
   ``revert_actions`` row, never zero. The ``snapshots_restored`` count
   on the row is the honest measure of "did this revert actually change
   state". A row with ``snapshots_restored = 0`` means the operator
   triggered a revert that was already done.
3. **Refuses to revert across a later sync.** If the target sync_job has
   snapshots that have been superseded by a NEWER sync_job (not by a
   revert), the revert is refused with :class:`RevertRefused`. Operators
   must roll back the newer sync first. Without this guardrail a revert
   would silently leave inconsistent state.

Canonical update behavior:

- **Prior snapshot exists and was not deleted upstream.** Rewind canonical
  to the prior snapshot's payload, clear ``deleted_at``.
- **Prior snapshot exists and was deleted upstream.** Rewind canonical to
  the prior snapshot's payload, set ``deleted_at`` to the prior snapshot's
  ``source_event_at`` (the moment upstream marked it deleted).
- **No prior snapshot.** The target sync_job created this entity. Soft
  delete the canonical row by setting ``deleted_at`` to the revert
  timestamp.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.canonical.entities import EntityType
from edlink_rostering.core.types import LeaId


@dataclass(frozen=True)
class RevertOutcome:
    """Result of one revert call.

    ``revert_generation_id`` is the synthetic UUID the revert tagged its
    reverted snapshots with. The operator can grep audit rows for this
    value to confirm what was undone.
    """

    revert_id: uuid.UUID
    sync_job_id: uuid.UUID
    revert_generation_id: uuid.UUID
    snapshots_restored: int
    canonical_rows_updated: int
    canonical_rows_soft_deleted: int


class RevertError(RuntimeError):
    """Base exception for revert refusal."""


class RevertSyncJobNotFound(RevertError):
    """The named sync_job_id does not exist."""


class RevertRefused(RevertError):
    """The target sync_job's snapshots have been superseded by a later
    sync. Roll back the later sync first."""


class RevertService:
    """Operator-driven revert.

    Connects via ``session_factory`` (typically ``ops_session_factory()``).
    Tests substitute a per-test session factory.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = session_factory

    async def revert(
        self,
        sync_job_id: uuid.UUID,
        operator_identity: str,
        reason: str,
    ) -> RevertOutcome:
        """Revert one sync_job.

        Writes a ``revert_actions`` row regardless of how many snapshots
        actually moved. The row carries ``snapshots_restored`` so the
        operator can see whether the revert was a no-op (double-click).
        """

        async with self._sessions() as session:
            now = datetime.now(UTC)
            target = await self._load_sync_job(session, sync_job_id)
            await self._refuse_if_superseded_by_newer_sync(
                session, sync_job_id
            )

            # The supersession FK requires generation IDs to live in
            # sync_jobs. Insert a synthetic sync_jobs row tagged
            # status='revert' so the FK is satisfied and operators see
            # reverts and syncs in one timeline.
            revert_generation_id = uuid.uuid4()
            await self._insert_revert_sync_job_row(
                session=session,
                revert_generation_id=revert_generation_id,
                target_sync_job_id=sync_job_id,
                target_lea_id=target.lea_id,
                target_partner=target.partner,
                started_at=now,
            )

            restored = 0
            updated = 0
            soft_deleted = 0

            for entity_type in (
                EntityType.STUDENT,
                EntityType.ENROLLMENT,
                EntityType.LEA,
            ):
                table = _snapshot_table(entity_type)
                key_col = _natural_key_column(entity_type)
                # Find every live snapshot inserted by this sync_job.
                # "Live" means superseded_by_generation_id IS NULL —
                # already-reverted snapshots have it pointing at a prior
                # revert_generation_id and we skip them.
                live = (
                    await session.execute(
                        text(
                            f"""
                            SELECT snapshot_id, {key_col} AS natural_key,
                                   lea_id, payload, deleted_upstream,
                                   source_event_id, source_event_at
                            FROM {table}
                            WHERE generation_id = :gen
                              AND superseded_by_generation_id IS NULL
                            ORDER BY source_event_id ASC NULLS FIRST
                            """
                        ),
                        {"gen": sync_job_id},
                    )
                ).all()

                for row in live:
                    natural_key = row.natural_key
                    lea_id = LeaId(row.lea_id)
                    prior = await self._find_prior_live_snapshot(
                        session=session,
                        entity_type=entity_type,
                        lea_id=lea_id,
                        natural_key=natural_key,
                        superseded_by=sync_job_id,
                    )

                    if prior is None:
                        # The target sync_job created this entity from
                        # scratch. Reverting means soft-deleting canonical.
                        await self._soft_delete_canonical(
                            session=session,
                            entity_type=entity_type,
                            natural_key=natural_key,
                            deleted_at=now,
                        )
                        soft_deleted += 1
                    else:
                        # Clear prior snapshot's supersession (restore it).
                        await session.execute(
                            text(
                                f"""
                                UPDATE {table}
                                SET superseded_by_generation_id = NULL,
                                    superseded_at = NULL
                                WHERE snapshot_id = :snap_id
                                """
                            ),
                            {"snap_id": prior.snapshot_id},
                        )
                        # Rewind canonical to prior's payload.
                        await self._restore_canonical_from_payload(
                            session=session,
                            entity_type=entity_type,
                            natural_key=natural_key,
                            lea_id=lea_id,
                            payload=prior.payload,
                            deleted_upstream=prior.deleted_upstream,
                            prior_source_event_at=prior.source_event_at,
                        )
                        updated += 1

                    # Mark this snapshot as reverted: superseded by the
                    # synthetic revert_generation_id. The original
                    # generation_id stays so the operator can trace which
                    # sync_job's snapshot was undone.
                    await session.execute(
                        text(
                            f"""
                            UPDATE {table}
                            SET superseded_by_generation_id = :rev_gen,
                                superseded_at = :superseded_at
                            WHERE snapshot_id = :snap_id
                            """
                        ),
                        {
                            "rev_gen": revert_generation_id,
                            "superseded_at": now,
                            "snap_id": row.snapshot_id,
                        },
                    )
                    restored += 1

            revert_id = await self._insert_revert_action(
                session=session,
                sync_job_id=sync_job_id,
                revert_generation_id=revert_generation_id,
                operator_identity=operator_identity,
                reason=reason,
                reverted_at=now,
                snapshots_restored=restored,
            )
            await session.commit()

            return RevertOutcome(
                revert_id=revert_id,
                sync_job_id=sync_job_id,
                revert_generation_id=revert_generation_id,
                snapshots_restored=restored,
                canonical_rows_updated=updated,
                canonical_rows_soft_deleted=soft_deleted,
            )

    async def _load_sync_job(
        self, session: AsyncSession, sync_job_id: uuid.UUID
    ) -> Any:
        row = (
            await session.execute(
                text(
                    "SELECT lea_id, partner FROM sync_jobs WHERE id = :id"
                ),
                {"id": sync_job_id},
            )
        ).first()
        if row is None:
            raise RevertSyncJobNotFound(
                f"sync_job_id {sync_job_id} not found"
            )
        return row

    async def _insert_revert_sync_job_row(
        self,
        *,
        session: AsyncSession,
        revert_generation_id: uuid.UUID,
        target_sync_job_id: uuid.UUID,
        target_lea_id: str,
        target_partner: str,
        started_at: datetime,
    ) -> None:
        """Synthetic sync_jobs row tagged ``status = 'revert'``.

        Lets the snapshot FK to ``sync_jobs.id`` stay enforced. The
        ``error_summary`` carries the target sync_job_id so operators can
        trace a revert back to what it undid without joining
        revert_actions.
        """

        await session.execute(
            text(
                """
                INSERT INTO sync_jobs (
                    id, lea_id, partner, status, started_at, completed_at,
                    event_count, error_summary
                ) VALUES (
                    :id, :lea_id, :partner, 'revert', :started_at,
                    :started_at, 0, :error_summary
                )
                """
            ),
            {
                "id": revert_generation_id,
                "lea_id": target_lea_id,
                "partner": target_partner,
                "started_at": started_at,
                "error_summary": (
                    f"revert of sync_job {target_sync_job_id}"
                ),
            },
        )

    async def _refuse_if_superseded_by_newer_sync(
        self, session: AsyncSession, sync_job_id: uuid.UUID
    ) -> None:
        """Refuse if any snapshot from this sync was superseded by another
        sync_job (not by a revert).

        A revert_generation_id is not a sync_jobs row, so the join below
        filters those out and only flags supersessions by real syncs.
        """

        for table in (
            "student_snapshots",
            "enrollment_snapshots",
            "lea_snapshots",
        ):
            row = (
                await session.execute(
                    text(
                        f"""
                        SELECT s.snapshot_id
                        FROM {table} s
                        JOIN sync_jobs j
                          ON j.id = s.superseded_by_generation_id
                         AND j.status != 'revert'
                        WHERE s.generation_id = :gen
                        LIMIT 1
                        """
                    ),
                    {"gen": sync_job_id},
                )
            ).first()
            if row is not None:
                raise RevertRefused(
                    f"sync_job_id {sync_job_id} has snapshots superseded "
                    f"by a later sync_job (table {table}). Roll back the "
                    "newer sync first."
                )

    async def _find_prior_live_snapshot(
        self,
        *,
        session: AsyncSession,
        entity_type: EntityType,
        lea_id: LeaId,
        natural_key: str,
        superseded_by: uuid.UUID,
    ) -> Any:
        """Return the snapshot that this sync_job superseded for a given
        natural key, or None if this sync_job introduced the entity.
        """

        table = _snapshot_table(entity_type)
        key_col = _natural_key_column(entity_type)
        return (
            await session.execute(
                text(
                    f"""
                    SELECT snapshot_id, payload, deleted_upstream,
                           source_event_id, source_event_at
                    FROM {table}
                    WHERE lea_id = :lea_id AND {key_col} = :nk
                      AND superseded_by_generation_id = :sup_by
                    ORDER BY source_event_id DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {
                    "lea_id": lea_id,
                    "nk": natural_key,
                    "sup_by": superseded_by,
                },
            )
        ).first()

    async def _soft_delete_canonical(
        self,
        *,
        session: AsyncSession,
        entity_type: EntityType,
        natural_key: str,
        deleted_at: datetime,
    ) -> None:
        canonical_table, id_col = _canonical_table_and_id(entity_type)
        await session.execute(
            text(
                f"""
                UPDATE {canonical_table}
                SET deleted_at = :deleted_at
                WHERE {id_col} = :nk
                """
            ),
            {"deleted_at": deleted_at, "nk": natural_key},
        )

    async def _restore_canonical_from_payload(
        self,
        *,
        session: AsyncSession,
        entity_type: EntityType,
        natural_key: str,
        lea_id: LeaId,
        payload: dict[str, Any],
        deleted_upstream: bool,
        prior_source_event_at: datetime | None,
    ) -> None:
        """UPSERT the canonical row from a snapshot's payload.

        If ``deleted_upstream`` is True the prior live state was a
        deletion; preserve that. Otherwise clear ``deleted_at`` so the
        canonical row is live again.
        """

        deleted_at = (
            prior_source_event_at if deleted_upstream else None
        )
        if entity_type == EntityType.STUDENT:
            await session.execute(
                text(
                    """
                    UPDATE students
                    SET lea_id = :lea_id,
                        given_name = :given_name,
                        family_name = :family_name,
                        grade = :grade,
                        preferred_first_name = :preferred,
                        primary_school_id = :school_id,
                        external_ids = CAST(:external_ids AS JSONB),
                        deleted_at = :deleted_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": natural_key,
                    "lea_id": payload.get("lea_id", lea_id),
                    "given_name": payload.get("given_name", ""),
                    "family_name": payload.get("family_name", ""),
                    "grade": payload.get("grade"),
                    "preferred": payload.get("preferred_first_name"),
                    "school_id": payload.get("primary_school_id"),
                    "external_ids": json.dumps(
                        payload.get("external_ids") or {}
                    ),
                    "deleted_at": deleted_at,
                },
            )
        elif entity_type == EntityType.ENROLLMENT:
            begin = payload.get("begin_date")
            end = payload.get("end_date")
            await session.execute(
                text(
                    """
                    UPDATE enrollments
                    SET lea_id = :lea_id,
                        student_id = :student_id,
                        class_id = :class_id,
                        begin_date = :begin_date,
                        end_date = :end_date,
                        deleted_at = :deleted_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": natural_key,
                    "lea_id": payload.get("lea_id", lea_id),
                    "student_id": payload.get("student_id"),
                    "class_id": payload.get("class_id"),
                    "begin_date": _parse_iso_date(begin),
                    "end_date": _parse_iso_date(end),
                    "deleted_at": deleted_at,
                },
            )
        elif entity_type == EntityType.LEA:
            await session.execute(
                text(
                    """
                    UPDATE leas
                    SET name = :name,
                        lea_type = :lea_type,
                        state = :state,
                        nces_lea_id = :nces,
                        deleted_at = :deleted_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": natural_key,
                    "name": payload.get("name", ""),
                    "lea_type": payload.get("lea_type", "traditional_district"),
                    "state": payload.get("state", "XX"),
                    "nces": payload.get("nces_lea_id"),
                    "deleted_at": deleted_at,
                },
            )

    async def _insert_revert_action(
        self,
        *,
        session: AsyncSession,
        sync_job_id: uuid.UUID,
        revert_generation_id: uuid.UUID,
        operator_identity: str,
        reason: str,
        reverted_at: datetime,
        snapshots_restored: int,
    ) -> uuid.UUID:
        revert_id = uuid.uuid4()
        await session.execute(
            text(
                """
                INSERT INTO revert_actions (
                    id, sync_job_id, revert_generation_id, operator_identity,
                    reason, reverted_at, snapshots_restored
                ) VALUES (
                    :id, :sync_job_id, :rev_gen, :operator, :reason,
                    :reverted_at, :restored
                )
                """
            ),
            {
                "id": revert_id,
                "sync_job_id": sync_job_id,
                "rev_gen": revert_generation_id,
                "operator": operator_identity,
                "reason": reason,
                "reverted_at": reverted_at,
                "restored": snapshots_restored,
            },
        )
        return revert_id


# ── Helpers ───────────────────────────────────────────────────────────────────


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

_CANONICAL_BY_ENTITY: dict[EntityType, tuple[str, str]] = {
    EntityType.LEA: ("leas", "id"),
    EntityType.STUDENT: ("students", "id"),
    EntityType.ENROLLMENT: ("enrollments", "id"),
}


def _snapshot_table(entity_type: EntityType) -> str:
    return _SNAPSHOT_TABLE_BY_ENTITY[entity_type]


def _natural_key_column(entity_type: EntityType) -> str:
    return _NATURAL_KEY_COLUMN_BY_ENTITY[entity_type]


def _canonical_table_and_id(entity_type: EntityType) -> tuple[str, str]:
    return _CANONICAL_BY_ENTITY[entity_type]


def _parse_iso_date(value: Any) -> date | None:
    """Turn an ISO date string from a snapshot payload back into a date."""

    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = [
    "RevertError",
    "RevertOutcome",
    "RevertRefused",
    "RevertService",
    "RevertSyncJobNotFound",
]
