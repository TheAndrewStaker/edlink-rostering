"""Query module for the leas dashboard roll-up.

One read function backs ``GET /api/leas``, returning a summary row
per LEA with student / enrollment counts, latest sync status, and
cursor lag. The router maps the typed dataclass below to a Pydantic
response model.

``cursor_lag_days`` is computed inside the query so the route handler
collapses to a one-liner mapping and the clock-now value is captured
once per request, matching the pattern in :mod:`.cursors`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId


@dataclass(frozen=True)
class LeaSummaryRow:
    """One LEA row with dashboard roll-up metrics."""

    id: str
    name: str
    lea_type: str
    state: str
    status: str
    student_count: int
    enrollment_count: int
    latest_sync_at: datetime | None
    latest_sync_status: str | None
    cursor_lag_days: float | None
    in_flight_count: int


_BASE_SQL = """
    SELECT
        l.id, l.name, l.lea_type, l.state, l.status,
        (
            SELECT COUNT(*) FROM students s
            WHERE s.lea_id = l.id AND s.deleted_at IS NULL
        ) AS student_count,
        (
            SELECT COUNT(*) FROM enrollments e
            WHERE e.lea_id = l.id AND e.deleted_at IS NULL
        ) AS enrollment_count,
        (
            SELECT MAX(started_at) FROM sync_jobs sj
            WHERE sj.lea_id = l.id AND sj.status != 'revert'
        ) AS latest_sync_at,
        (
            SELECT status FROM sync_jobs sj2
            WHERE sj2.lea_id = l.id AND sj2.status != 'revert'
            ORDER BY started_at DESC LIMIT 1
        ) AS latest_sync_status,
        (
            SELECT last_event_at FROM cursor_state c
            WHERE c.lea_id = l.id LIMIT 1
        ) AS cursor_last_event_at,
        (
            SELECT COUNT(*) FROM sync_jobs sj3
            WHERE sj3.lea_id = l.id
              AND sj3.status = 'running'
        ) AS in_flight_count
    FROM leas l
    WHERE l.deleted_at IS NULL
"""


def _to_row(raw: Any, *, now: datetime) -> LeaSummaryRow:
    last_event_at: datetime | None = raw.cursor_last_event_at
    cursor_lag_days: float | None = None
    if last_event_at is not None:
        cursor_lag_days = (now - last_event_at).total_seconds() / 86400.0
    return LeaSummaryRow(
        id=raw.id,
        name=raw.name,
        lea_type=raw.lea_type,
        state=raw.state,
        status=raw.status,
        student_count=int(raw.student_count),
        enrollment_count=int(raw.enrollment_count),
        latest_sync_at=raw.latest_sync_at,
        latest_sync_status=raw.latest_sync_status,
        cursor_lag_days=cursor_lag_days,
        in_flight_count=int(raw.in_flight_count or 0),
    )


async def list_leas(
    factory: async_sessionmaker[AsyncSession],
    *,
    authorized_leas: frozenset[LeaId] | None = None,
) -> list[LeaSummaryRow]:
    """All known LEAs with dashboard roll-up metrics, sorted by id.

    ``authorized_leas=None`` means "no scope filter" (used by
    owner, admin, auditor whose role implies all
    LEAs). A non-None set scopes the result, which is how the operator
    role's list lands without leaking other LEAs' names + counts. Same
    pattern as
    :meth:`edlink_rostering.services.connector_authz.ConnectorAuthorizationService.list_authorizations`.
    """

    sql = _BASE_SQL
    params: dict[str, Any] = {}
    if authorized_leas is not None:
        sql += " AND l.id = ANY(:leas)"
        params["leas"] = list(authorized_leas)
    sql += " ORDER BY l.id"

    async with factory() as session:
        rows = (await session.execute(text(sql), params)).all()
    now = datetime.now(UTC)
    return [_to_row(r, now=now) for r in rows]


__all__ = ["LeaSummaryRow", "list_leas"]
