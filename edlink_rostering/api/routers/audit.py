"""Cross-LEA audit explorer endpoint.

The Phase 2 founder admin surface from
``docs/design/admin-surfaces.md`` § "Audit log". Returns a paginated
slice of the unified activity timeline, filterable by operator,
action prefix, and time window. The per-LEA endpoint
(``/api/leas/{lea_id}/timeline``) is the same UNION constrained to
one LEA.

Auth: ``require("auditor")`` so auditor, operator, admin,
and owner can read. LEA scope filtering follows the
``operator`` role's authorized-LEA set; the other roles see every
LEA. The role gate is the only auth surface; per-LEA scope filtering
is a defense-in-depth layer beneath it.

Pagination is cursor-based. The client passes back the ``next_cursor``
from the prior response to fetch the following page; the cursor is
the (occurred_at, id) pair of the last entry seen. No total count is
returned because totals across all sources require a separate
COUNT(*) round trip per branch and the operator never needs one for
incident response or compliance review.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.auth import Operator, require
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.schemas import (
    AuditCursor,
    AuditExplorerPage,
    TimelineEntryOut,
)
from edlink_rostering.services.admin_timeline import (
    TimelineEntry,
    TimelineFilter,
    list_timeline,
)


router = APIRouter(prefix="/admin", tags=["audit"])


@router.get(
    "/audit",
    response_model=AuditExplorerPage,
    operation_id="audit.list_entries",
)
async def list_audit_entries(
    operator_id: str | None = Query(None),
    action_prefix: str | None = Query(
        None,
        description=(
            "Filter to actions starting with this prefix (e.g."
            " 'sync.', 'reconciliation.', 'connector.')"
        ),
    ),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    cursor_occurred_at: datetime | None = Query(None),
    cursor_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    op: Operator = Depends(require("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AuditExplorerPage:
    """Return one page of the cross-LEA audit timeline newest-first."""

    # operator role gets the explicit grant set; the other roles
    # (auditor, admin, owner) get the implicit
    # all-LEAs scope which maps to lea_ids=None inside the service.
    scope = op.authorized_leas if op.role == "operator" else None

    # Defensive: a future role mapping might leave authorized_leas
    # empty for an unmapped role. Refuse to broaden to "all LEAs"
    # silently — return an empty page so the operator's request is
    # honest about scope.
    if op.role == "operator" and not scope:
        return AuditExplorerPage(entries=[], next_cursor=None)

    filt = TimelineFilter(
        lea_ids=scope,
        operator_id=operator_id,
        action_prefix=action_prefix,
        since=since,
        until=until,
        cursor_occurred_at=cursor_occurred_at,
        cursor_id=cursor_id,
        limit=limit,
    )
    entries = await list_timeline(factory, filt)
    return AuditExplorerPage(
        entries=[_entry_to_out(e) for e in entries],
        next_cursor=_next_cursor(entries, limit),
    )


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


def _next_cursor(
    entries: list[TimelineEntry], limit: int
) -> AuditCursor | None:
    """Build a cursor from the last entry, or None when the page wasn't full.

    The cursor encodes the oldest entry in this page (last in
    newest-first order). When the page returned fewer than ``limit``
    entries we know no more pages exist; signal that with ``None``.
    """

    if len(entries) < limit:
        return None
    last = entries[-1]
    return AuditCursor(occurred_at=last.occurred_at, id=last.id)


__all__ = ["router"]
