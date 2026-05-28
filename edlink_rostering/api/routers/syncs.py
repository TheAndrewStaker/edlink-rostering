"""Sync timeline and per-sync detail endpoints.

SQL lives in :mod:`edlink_rostering.services.queries.syncs`; this router maps
the typed query-module results to Pydantic response models and
handles HTTP plumbing. Splitting SQL out of the router makes the
queries unit-testable against a real Postgres without spinning up a
TestClient and centralizes the per-LEA invariant in one place.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.auth import Operator, require, require_lea_scope_at
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.schemas import (
    RetryHistoryRow,
    RevertHistoryRow,
    SyncActivityBucket,
    SyncJobDetail,
    SyncJobSummary,
    ValidationIssueRow,
)
from edlink_rostering.services.queries.syncs import (
    SyncDetail,
    SyncJobRow,
    get_sync_activity,
    get_sync_detail,
    list_syncs_for_lea,
)

router = APIRouter(tags=["syncs"])


def _to_summary(row: SyncJobRow) -> SyncJobSummary:
    return SyncJobSummary(
        id=row.id,
        lea_id=row.lea_id,
        partner=row.partner,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        event_count=row.event_count,
        error_count=row.error_count,
        warning_count=row.warning_count,
        cursor_before=row.cursor_before,
        cursor_after=row.cursor_after,
        error_summary=row.error_summary,
    )


def _to_detail(detail: SyncDetail) -> SyncJobDetail:
    return SyncJobDetail(
        sync=_to_summary(detail.sync),
        validation_issues=[
            ValidationIssueRow(
                layer=v.layer,
                code=v.code,
                payload_reference=v.payload_reference,
                detail=v.detail,
                created_at=v.created_at,
            )
            for v in detail.validation_issues
        ],
        quarantined_entity_ids=detail.quarantined_entity_ids,
        revert_history=[
            RevertHistoryRow(
                id=r.id,
                operator_identity=r.operator_identity,
                reason=r.reason,
                reverted_at=r.reverted_at,
                snapshots_restored=r.snapshots_restored,
            )
            for r in detail.revert_history
        ],
        retry_history=[
            RetryHistoryRow(
                id=r.id,
                operator_identity=r.operator_identity,
                reason=r.reason,
                retried_at=r.retried_at,
                cursor_rewound_to=r.cursor_rewound_to,
                forced=r.forced,
            )
            for r in detail.retry_history
        ],
    )


@router.get(
    "/syncs/activity",
    response_model=list[SyncActivityBucket],
    operation_id="syncs.activity",
)
async def sync_activity(
    op: Operator = Depends(require("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[SyncActivityBucket]:
    """Hourly sync outcome counts for the last 24 hours (cross-LEA).

    Powers the dashboard's Sync Activity chart. Groups completed
    sync_jobs into hour buckets with success/warning/failed counts.
    Role gate: ``auditor``.
    """

    _ = op
    buckets = await get_sync_activity(factory)
    return [
        SyncActivityBucket(
            hour=b.hour,
            success=b.success,
            warning=b.warning,
            failed=b.failed,
        )
        for b in buckets
    ]


@router.get(
    "/leas/{lea_id}/syncs",
    response_model=list[SyncJobSummary],
    operation_id="syncs.list_for_lea",
)
async def list_syncs(
    lea_id: str,
    limit: int = Query(20, ge=1, le=200),
    partner: str = Query("edlink"),
    op: Operator = Depends(require_lea_scope_at("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[SyncJobSummary]:
    """Recent sync_jobs for one LEA, newest first.

    Role gate: ``auditor`` plus per-LEA scope enforcement for the
    ``operator`` role (auditor / admin / owner are
    implicitly scoped to every active LEA per V0005, so they skip
    the scope check and an unknown LEA returns an empty list).
    """

    _ = op
    rows = await list_syncs_for_lea(
        factory, lea_id=lea_id, partner=partner, limit=limit
    )
    return [_to_summary(r) for r in rows]


@router.get(
    "/syncs/{sync_job_id}",
    response_model=SyncJobDetail,
    operation_id="syncs.get_detail",
)
async def get_sync_detail_route(
    sync_job_id: uuid.UUID,
    op: Operator = Depends(require("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> SyncJobDetail:
    """Full detail for one sync_job. Powers the drill-down drawer.

    Role gate: ``auditor``. The path key is the sync_job_id rather
    than the LEA, so per-LEA scope enforcement after lookup is a
    follow-up: it would require fetching the row first to learn the
    LEA, then 403'ing on out-of-scope, which leaks existence to
    out-of-scope operators (a 404 collapses to a 403 only when they
    happen to know a valid id). Acceptable risk on the POC path.
    """

    _ = op
    detail = await get_sync_detail(factory, sync_job_id=sync_job_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"sync_job_id {sync_job_id} not found",
        )
    return _to_detail(detail)
