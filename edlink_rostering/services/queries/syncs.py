"""Query module for the syncs aggregate.

Two read functions back the ``/api/leas/{lea_id}/syncs`` and
``/api/syncs/{sync_job_id}`` router endpoints. The router maps the
typed dataclasses below to Pydantic response models; no SQL lives in
the router.

The detail query intentionally returns a single composed dataclass
rather than five separate calls so the route handler is a one-liner.
Five separate per-aggregate calls would be cleaner if any caller
needed only part of the bundle, but no caller does today.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class SyncJobRow:
    """One sync_jobs row in the shape the API surfaces."""

    id: uuid.UUID
    lea_id: str
    partner: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    event_count: int
    error_count: int
    warning_count: int
    cursor_before: str | None
    cursor_after: str | None
    error_summary: str | None


@dataclass(frozen=True)
class ValidationIssueRow:
    layer: int
    code: str
    payload_reference: str | None
    detail: dict[str, object] | None
    created_at: datetime


@dataclass(frozen=True)
class RevertActionRow:
    id: uuid.UUID
    operator_identity: str
    reason: str
    reverted_at: datetime
    snapshots_restored: int


@dataclass(frozen=True)
class RetryActionRow:
    id: uuid.UUID
    operator_identity: str
    reason: str
    retried_at: datetime
    cursor_rewound_to: str | None
    forced: bool


@dataclass(frozen=True)
class SyncDetail:
    """Composed shape for the per-sync drill-down."""

    sync: SyncJobRow
    validation_issues: list[ValidationIssueRow]
    quarantined_entity_ids: list[str]
    revert_history: list[RevertActionRow]
    retry_history: list[RetryActionRow]


_LIST_SQL = text(
    """
    SELECT id, lea_id, partner, status, started_at,
           completed_at, event_count, error_count,
           warning_count, cursor_before, cursor_after,
           error_summary
    FROM sync_jobs
    WHERE lea_id = :lea AND partner = :partner
    ORDER BY started_at DESC
    LIMIT :limit
    """
)

_DETAIL_SQL = text(
    """
    SELECT id, lea_id, partner, status, started_at,
           completed_at, event_count, error_count,
           warning_count, cursor_before, cursor_after,
           error_summary
    FROM sync_jobs WHERE id = :id
    """
)

_VALIDATION_SQL = text(
    """
    SELECT layer, code, payload_reference, detail, created_at
    FROM sync_validation_results
    WHERE sync_job_id = :id
    ORDER BY layer, created_at
    """
)

_QUARANTINE_SQL = text(
    """
    SELECT entity_id FROM quarantine
    WHERE sync_job_id = :id
    """
)

_REVERT_SQL = text(
    """
    SELECT id, operator_identity, reason, reverted_at,
           snapshots_restored
    FROM revert_actions WHERE sync_job_id = :id
    ORDER BY reverted_at
    """
)

_RETRY_SQL = text(
    """
    SELECT id, operator_identity, reason, retried_at,
           cursor_rewound_to, forced
    FROM retry_actions WHERE sync_job_id = :id
    ORDER BY retried_at
    """
)


def _to_sync_job_row(row: Any) -> SyncJobRow:
    return SyncJobRow(
        id=row.id,
        lea_id=row.lea_id,
        partner=row.partner,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        event_count=int(row.event_count),
        error_count=int(row.error_count),
        warning_count=int(row.warning_count),
        cursor_before=row.cursor_before,
        cursor_after=row.cursor_after,
        error_summary=row.error_summary,
    )


async def list_syncs_for_lea(
    factory: async_sessionmaker[AsyncSession],
    *,
    lea_id: str,
    partner: str,
    limit: int,
) -> list[SyncJobRow]:
    """Recent sync_jobs for one LEA + partner, newest first."""

    async with factory() as session:
        rows = (
            await session.execute(
                _LIST_SQL,
                {"lea": lea_id, "partner": partner, "limit": limit},
            )
        ).all()
    return [_to_sync_job_row(r) for r in rows]


async def get_sync_detail(
    factory: async_sessionmaker[AsyncSession],
    *,
    sync_job_id: uuid.UUID,
) -> SyncDetail | None:
    """Full per-sync detail (job + validation + quarantine + history).

    Returns ``None`` for an unknown id so the router maps to 404.
    """

    async with factory() as session:
        sync_row = (
            await session.execute(_DETAIL_SQL, {"id": sync_job_id})
        ).first()
        if sync_row is None:
            return None
        sync = _to_sync_job_row(sync_row)

        validation_rows = (
            await session.execute(_VALIDATION_SQL, {"id": sync_job_id})
        ).all()
        issues = [
            ValidationIssueRow(
                layer=int(v.layer),
                code=v.code,
                payload_reference=v.payload_reference,
                detail=v.detail,
                created_at=v.created_at,
            )
            for v in validation_rows
        ]

        q_rows = (
            await session.execute(_QUARANTINE_SQL, {"id": sync_job_id})
        ).all()
        quarantine_ids = [q.entity_id for q in q_rows]

        revert_rows = (
            await session.execute(_REVERT_SQL, {"id": sync_job_id})
        ).all()
        revert_history = [
            RevertActionRow(
                id=r.id,
                operator_identity=r.operator_identity,
                reason=r.reason,
                reverted_at=r.reverted_at,
                snapshots_restored=int(r.snapshots_restored),
            )
            for r in revert_rows
        ]

        retry_rows = (
            await session.execute(_RETRY_SQL, {"id": sync_job_id})
        ).all()
        retry_history = [
            RetryActionRow(
                id=r.id,
                operator_identity=r.operator_identity,
                reason=r.reason,
                retried_at=r.retried_at,
                cursor_rewound_to=r.cursor_rewound_to,
                forced=bool(r.forced),
            )
            for r in retry_rows
        ]

    return SyncDetail(
        sync=sync,
        validation_issues=issues,
        quarantined_entity_ids=quarantine_ids,
        revert_history=revert_history,
        retry_history=retry_history,
    )


@dataclass(frozen=True)
class SyncActivityBucket:
    """One hour-bucket for the sync activity chart."""

    hour: datetime
    success: int
    warning: int
    failed: int


_ACTIVITY_SQL = text(
    """
    SELECT
        DATE_TRUNC('hour', started_at) AS hour,
        COUNT(*) FILTER (
            WHERE status = 'success' AND warning_count = 0
        ) AS success,
        COUNT(*) FILTER (
            WHERE status = 'success' AND warning_count > 0
        ) AS warning,
        COUNT(*) FILTER (
            WHERE status = 'failed'
        ) AS failed
    FROM sync_jobs
    WHERE started_at >= NOW() - INTERVAL '24 hours'
      AND status != 'running'
    GROUP BY hour
    ORDER BY hour
    """
)


async def get_sync_activity(
    factory: async_sessionmaker[AsyncSession],
) -> list[SyncActivityBucket]:
    """Hourly sync outcome buckets for the last 24 hours (cross-LEA)."""

    async with factory() as session:
        rows = (await session.execute(_ACTIVITY_SQL)).all()
    return [
        SyncActivityBucket(
            hour=r.hour,
            success=int(r.success),
            warning=int(r.warning),
            failed=int(r.failed),
        )
        for r in rows
    ]


__all__ = [
    "RetryActionRow",
    "RevertActionRow",
    "SyncActivityBucket",
    "SyncDetail",
    "SyncJobRow",
    "ValidationIssueRow",
    "get_sync_activity",
    "get_sync_detail",
    "list_syncs_for_lea",
]
