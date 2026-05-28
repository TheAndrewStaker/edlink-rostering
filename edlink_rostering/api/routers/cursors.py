"""Cursor-status endpoints.

SQL lives in :mod:`edlink_rostering.services.queries.cursors`; this router
maps the typed query-module rows to Pydantic response models.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.auth import Operator, require
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.schemas import CursorStateRow
from edlink_rostering.core.types import LeaId
from edlink_rostering.services.queries.cursors import (
    CursorStateRow as CursorStateRowDC,
    list_cursors,
)

router = APIRouter(tags=["cursors"])


def _to_out(row: CursorStateRowDC) -> CursorStateRow:
    return CursorStateRow(
        lea_id=row.lea_id,
        partner=row.partner,
        last_event_id=row.last_event_id,
        last_event_at=row.last_event_at,
        last_poll_at=row.last_poll_at,
        cold_start_required=row.cold_start_required,
        days_behind=row.days_behind,
    )


@router.get(
    "/cursors",
    response_model=list[CursorStateRow],
    operation_id="cursors.list",
)
async def list_cursors_route(
    lea_id: str | None = Query(default=None),
    op: Operator = Depends(require("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[CursorStateRow]:
    """All cursors with computed ``days_behind``.

    Mirrors the ``cursor-status`` CLI command. The 20-day-behind flag
    is computed on the client (admin app); this endpoint just returns
    the raw ``days_behind`` so the UI can display the value, the
    badge, and any future tunable threshold without an API change.

    Role gate: ``auditor``. The ``operator`` role sees only LEAs in
    its grant set per V0005; the other roles see every cursor. An
    operator passing ``lea_id`` for an LEA outside their grant gets
    an empty list (the query filter composes with the scope filter).
    """

    scope: frozenset[LeaId] | None
    if op.role == "operator":
        scope = op.authorized_leas
        if not scope:
            return []
    else:
        scope = None

    rows = await list_cursors(factory, lea_id=lea_id, authorized_leas=scope)
    return [_to_out(r) for r in rows]
