"""Query module for the cursor_state aggregate.

One read function backs ``GET /api/cursors``. The router maps the
typed dataclass below to a Pydantic response model and handles the
``days_behind`` derivation; no SQL lives in the router.

``days_behind`` is computed inside the query module so the router
collapses to a one-liner mapping. Computing it here also means the
clock-now value is captured once per request rather than per row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId


@dataclass(frozen=True)
class CursorStateRow:
    """One ``cursor_state`` row in the shape the API surfaces."""

    lea_id: str
    partner: str
    last_event_id: str | None
    last_event_at: datetime | None
    last_poll_at: datetime | None
    cold_start_required: bool
    days_behind: float | None


_BASE_SQL = """
    SELECT lea_id, partner, last_event_id, last_event_at, last_poll_at,
           cold_start_required
    FROM cursor_state
"""


def _to_row(raw: Any, *, now: datetime) -> CursorStateRow:
    last_event_at: datetime | None = raw.last_event_at
    days_behind: float | None = None
    if last_event_at is not None:
        days_behind = (now - last_event_at).total_seconds() / 86400.0
    return CursorStateRow(
        lea_id=raw.lea_id,
        partner=raw.partner,
        last_event_id=raw.last_event_id,
        last_event_at=last_event_at,
        last_poll_at=raw.last_poll_at,
        cold_start_required=bool(raw.cold_start_required),
        days_behind=days_behind,
    )


async def list_cursors(
    factory: async_sessionmaker[AsyncSession],
    *,
    lea_id: str | None = None,
    authorized_leas: frozenset[LeaId] | None = None,
) -> list[CursorStateRow]:
    """All cursors, with optional single-LEA filter and operator-scope filter.

    ``lea_id`` is the request-supplied query parameter (any role can
    narrow the listing to one LEA). ``authorized_leas`` is the
    operator-role scope; passing ``None`` means "no scope filter"
    (owner, admin, auditor see every LEA). The two
    compose: an operator passing a ``lea_id`` for an LEA outside their
    grant gets an empty result set.
    """

    sql = _BASE_SQL
    params: dict[str, Any] = {}
    where: list[str] = []
    if lea_id is not None:
        where.append("lea_id = :lea")
        params["lea"] = lea_id
    if authorized_leas is not None:
        where.append("lea_id = ANY(:leas)")
        params["leas"] = list(authorized_leas)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY lea_id, partner"

    async with factory() as session:
        rows = (await session.execute(text(sql), params)).all()
    now = datetime.now(UTC)
    return [_to_row(r, now=now) for r in rows]


__all__ = ["CursorStateRow", "list_cursors"]
