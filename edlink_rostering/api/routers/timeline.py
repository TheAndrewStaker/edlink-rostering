"""Per-LEA activity timeline endpoint.

The read side of the Phase 2 audit-log explorer scoped to one LEA.
Returns a normalized UNION across ``audit_log``, ``sync_jobs``,
``revert_actions``, ``retry_actions``, ``quarantine``, and
``reconciliation_runs`` so the drawer panel renders one timeline
instead of N per-source tables.

Auth follows the pattern of the other per-LEA read endpoints
(``/syncs``, ``/cursors``, ``/quarantine``, ``/reconciliation``):
the ``auditor`` role gate is the only check; per-operator LEA
scoping is enforced upstream on the LEA list, not on read
drill-downs. Mutation endpoints retain the per-LEA scope check.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.auth import Operator, require_lea_scope_at
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.schemas import TimelineEntryOut
from edlink_rostering.core.types import LeaId
from edlink_rostering.services.admin_timeline import (
    TimelineEntry,
    list_timeline_for_lea,
)


router = APIRouter(tags=["timeline"])


@router.get(
    "/leas/{lea_id}/timeline",
    response_model=list[TimelineEntryOut],
    operation_id="timeline.list_for_lea",
)
async def list_lea_timeline(
    lea_id: str,
    limit: int = Query(50, ge=1, le=200),
    op: Operator = Depends(require_lea_scope_at("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[TimelineEntryOut]:
    """Return the per-LEA activity timeline newest-first.

    Empty list (not 404) for an LEA with no activity yet; matches the
    existing per-LEA read endpoints to avoid info-disclosure on
    existence. Operators not scoped to the target LEA get a 403 from
    ``require_lea_scope_at`` before this body runs.
    """

    _ = op
    entries = await list_timeline_for_lea(factory, LeaId(lea_id), limit=limit)
    return [_entry_to_out(e) for e in entries]


def _entry_to_out(entry: TimelineEntry) -> TimelineEntryOut:
    return TimelineEntryOut(
        id=entry.id,
        source=entry.source,
        occurred_at=entry.occurred_at,
        actor_kind=entry.actor_kind,
        actor_email=entry.actor_email,
        action=entry.action,
        reason=entry.reason,
        target_kind=entry.target_kind,
        target_id=entry.target_id,
        detail=entry.detail,
    )


__all__ = ["router"]
