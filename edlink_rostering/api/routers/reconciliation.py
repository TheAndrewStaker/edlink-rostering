"""Reconciliation history endpoint.

Per-LEA list of recent ``reconciliation_runs`` rows for the admin
app's drawer panel. Read-only; the daily sweep and operator-driven
forced reconciles are the only writers (via
:class:`edlink_rostering.services.reconciliation.ReconciliationService`).

SQL lives in :mod:`edlink_rostering.services.queries.reconciliation`; this
router maps the typed query-module rows to Pydantic response models
and enforces the per-LEA scope check.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.auth import Operator, require_lea_scope_at
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.schemas import (
    ReconciliationDriftDetailOut,
    ReconciliationRunRow,
)
from edlink_rostering.services.queries.reconciliation import (
    DriftDetailRow,
    ReconciliationRunRow as ReconciliationRunRowDC,
    list_reconciliation_runs_for_lea,
)


router = APIRouter(tags=["reconciliation"])


def _to_drift_out(d: DriftDetailRow) -> ReconciliationDriftDetailOut:
    return ReconciliationDriftDetailOut(
        entity_type=d.entity_type,
        canonical_only_ids=d.canonical_only_ids,
        partner_only_ids=d.partner_only_ids,
        canonical_mid_hash=d.canonical_mid_hash,
        partner_mid_hash=d.partner_mid_hash,
    )


def _to_out(row: ReconciliationRunRowDC) -> ReconciliationRunRow:
    return ReconciliationRunRow(
        id=row.id,
        lea_id=row.lea_id,
        partner=row.partner,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        canonical_root_hash=row.canonical_root_hash,
        partner_root_hash=row.partner_root_hash,
        drift=[_to_drift_out(d) for d in row.drift],
        error_message=row.error_message,
    )


@router.get(
    "/leas/{lea_id}/reconciliation",
    response_model=list[ReconciliationRunRow],
    operation_id="reconciliation.list_for_lea",
)
async def list_reconciliation_runs(
    lea_id: str,
    limit: int = Query(20, ge=1, le=200),
    partner: str = Query("edlink"),
    op: Operator = Depends(require_lea_scope_at("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[ReconciliationRunRow]:
    """Recent reconciliation runs for one LEA, newest first.

    Returns an empty list for an LEA with no run history rather than
    a 404, matching the existing per-LEA read endpoints in this API
    (``/leas/{lea_id}/syncs``, ``/leas/{lea_id}/cursors``). Operators
    not scoped to the target LEA get a 403 from
    ``require_lea_scope_at`` before this body runs.
    """

    _ = op  # role + scope gate; op identity is not used below
    rows = await list_reconciliation_runs_for_lea(
        factory, lea_id=lea_id, partner=partner, limit=limit
    )
    return [_to_out(r) for r in rows]


__all__ = ["router"]
