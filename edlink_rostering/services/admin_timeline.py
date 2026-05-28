"""Per-LEA activity timeline.

The Phase 2 audit-log explorer from ``docs/design/admin-surfaces.md``
unioned at read time into one chronological stream. Six sources land
on the same shape so the drawer panel renders a single timeline:

* ``audit_log`` (V0004) for operator actions that do not have their
  own audit table (connector lifecycle today; founder admin and break
  glass once they ship).
* ``sync_jobs`` (V0001) for each sync attempt's terminal status.
* ``revert_actions`` (V0001) for operator-initiated reverts.
* ``retry_actions`` (V0003) for operator-initiated retries.
* ``quarantine`` (V0001) twice: once per row at ``created_at`` and a
  second time at ``resolved_at`` when the operator releases or
  rejects it.
* ``reconciliation_runs`` (V0006) for each scheduled or forced run.

The UNION lives at the SQL layer because the design doc commits to
that pattern. Adding a new source means adding one CTE branch here
and one label in the frontend; no per-source endpoint or service
sprawl.

``actor_kind`` is ``operator`` when a human triggered the row and
``system`` when the sync worker or scheduler did. The actor email
column resolves to the operator's email when we can (``audit_log``
joins ``operator`` on ``operator_id``); legacy text identifiers in
``revert_actions``/``retry_actions``/``quarantine.resolution_operator``
flow through unchanged because they predate the operator table and
were never upgraded.

Only LEA-scoped sources show up. ``audit_log`` rows with
``lea_id IS NULL`` (system-wide actions like an operator role grant
not bound to one LEA) are intentionally excluded from the per-LEA
view; they will surface in the global admin explorer when that
ships.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId


TimelineSource = Literal[
    "audit_log",
    "sync_jobs",
    "revert_actions",
    "retry_actions",
    "quarantine_created",
    "quarantine_resolved",
    "reconciliation_runs",
]

ActorKind = Literal["operator", "system"]


@dataclass(frozen=True)
class TimelineFilter:
    """Composable filter for the global activity timeline.

    Every field is optional so the same query backs the per-LEA
    drawer panel (``lea_ids = {one}``) and the cross-LEA founder
    explorer (``lea_ids = None``). The cursor pair is the
    occurred-at + id of the last entry the client received; the
    query returns entries strictly older than the cursor.
    """

    lea_ids: frozenset[LeaId] | None = None
    operator_id: str | None = None
    action_prefix: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    cursor_occurred_at: datetime | None = None
    cursor_id: str | None = None
    limit: int = 50


@dataclass(frozen=True)
class TimelineEntry:
    """One normalized row in the per-LEA activity timeline."""

    id: str
    source: TimelineSource
    occurred_at: datetime
    actor_kind: ActorKind
    actor_email: str | None
    action: str
    reason: str | None
    target_kind: str
    target_id: str
    detail: dict[str, Any] | None


# Six UNION branches, each producing the same column shape. Each
# branch's ``id`` is unique across the union: the row's primary key
# from its source table is enough, except for ``quarantine`` which
# contributes two timeline entries from one row and disambiguates
# with a ``:created`` / ``:resolved`` suffix.
_TIMELINE_SQL = """
WITH audit AS (
    SELECT
        al.id::text AS id,
        'audit_log'::text AS source,
        al.created_at AS occurred_at,
        'operator'::text AS actor_kind,
        op.email AS actor_email,
        al.action AS action,
        al.reason AS reason,
        al.target_kind AS target_kind,
        al.target_id AS target_id,
        al.detail AS detail
    FROM audit_log al
    LEFT JOIN operator op ON op.id = al.operator_id
    WHERE al.lea_id = :lea
),
syncs AS (
    SELECT
        sj.id::text AS id,
        'sync_jobs'::text AS source,
        sj.started_at AS occurred_at,
        'system'::text AS actor_kind,
        NULL::text AS actor_email,
        ('sync.' || sj.status) AS action,
        sj.error_summary AS reason,
        'sync_job'::text AS target_kind,
        sj.id::text AS target_id,
        jsonb_build_object(
            'partner', sj.partner,
            'event_count', sj.event_count,
            'error_count', sj.error_count,
            'warning_count', sj.warning_count,
            'completed_at', sj.completed_at,
            'cursor_before', sj.cursor_before,
            'cursor_after', sj.cursor_after
        ) AS detail
    FROM sync_jobs sj
    WHERE sj.lea_id = :lea
),
reverts AS (
    SELECT
        ra.id::text AS id,
        'revert_actions'::text AS source,
        ra.reverted_at AS occurred_at,
        'operator'::text AS actor_kind,
        ra.operator_identity AS actor_email,
        'sync.revert'::text AS action,
        ra.reason AS reason,
        'sync_job'::text AS target_kind,
        ra.sync_job_id::text AS target_id,
        jsonb_build_object(
            'snapshots_restored', ra.snapshots_restored,
            'revert_generation_id', ra.revert_generation_id::text
        ) AS detail
    FROM revert_actions ra
    JOIN sync_jobs sj ON sj.id = ra.sync_job_id
    WHERE sj.lea_id = :lea
),
retries AS (
    SELECT
        rt.id::text AS id,
        'retry_actions'::text AS source,
        rt.retried_at AS occurred_at,
        'operator'::text AS actor_kind,
        rt.operator_identity AS actor_email,
        'sync.retry_requested'::text AS action,
        rt.reason AS reason,
        'sync_job'::text AS target_kind,
        rt.sync_job_id::text AS target_id,
        jsonb_build_object(
            'partner', rt.partner,
            'forced', rt.forced,
            'cursor_rewound_to', rt.cursor_rewound_to
        ) AS detail
    FROM retry_actions rt
    WHERE rt.lea_id = :lea
),
quarantine_created AS (
    SELECT
        (q.id::text || '#created') AS id,
        'quarantine_created'::text AS source,
        q.created_at AS occurred_at,
        'system'::text AS actor_kind,
        NULL::text AS actor_email,
        'quarantine.created'::text AS action,
        q.reason AS reason,
        'quarantine_row'::text AS target_kind,
        q.id::text AS target_id,
        jsonb_build_object(
            'entity_type', q.entity_type,
            'entity_id', q.entity_id,
            'sync_job_id', q.sync_job_id::text
        ) AS detail
    FROM quarantine q
    WHERE q.lea_id = :lea
),
quarantine_resolved AS (
    SELECT
        (q.id::text || '#resolved') AS id,
        'quarantine_resolved'::text AS source,
        q.resolved_at AS occurred_at,
        'operator'::text AS actor_kind,
        q.resolution_operator AS actor_email,
        ('quarantine.' || COALESCE(q.resolution_status, 'resolved')) AS action,
        NULL::text AS reason,
        'quarantine_row'::text AS target_kind,
        q.id::text AS target_id,
        jsonb_build_object(
            'entity_type', q.entity_type,
            'entity_id', q.entity_id,
            'resolution_status', q.resolution_status
        ) AS detail
    FROM quarantine q
    WHERE q.lea_id = :lea AND q.resolved_at IS NOT NULL
),
reconciliations AS (
    SELECT
        rr.id::text AS id,
        'reconciliation_runs'::text AS source,
        rr.started_at AS occurred_at,
        'system'::text AS actor_kind,
        NULL::text AS actor_email,
        ('reconciliation.' || rr.status) AS action,
        rr.error_message AS reason,
        'reconciliation_run'::text AS target_kind,
        rr.id::text AS target_id,
        jsonb_build_object(
            'partner', rr.partner,
            'canonical_root_hash', rr.canonical_root_hash,
            'partner_root_hash', rr.partner_root_hash,
            'completed_at', rr.completed_at,
            'drift_count', COALESCE(
                jsonb_array_length(COALESCE(rr.drift_summary, '[]'::jsonb)),
                0
            )
        ) AS detail
    FROM reconciliation_runs rr
    WHERE rr.lea_id = :lea
)
SELECT id, source, occurred_at, actor_kind, actor_email,
       action, reason, target_kind, target_id, detail
FROM (
    SELECT * FROM audit
    UNION ALL SELECT * FROM syncs
    UNION ALL SELECT * FROM reverts
    UNION ALL SELECT * FROM retries
    UNION ALL SELECT * FROM quarantine_created
    UNION ALL SELECT * FROM quarantine_resolved
    UNION ALL SELECT * FROM reconciliations
) AS combined
ORDER BY occurred_at DESC
LIMIT :limit
"""


async def list_timeline_for_lea(
    session_factory: async_sessionmaker[AsyncSession],
    lea_id: LeaId,
    *,
    limit: int = 50,
) -> list[TimelineEntry]:
    """Return the per-LEA activity timeline newest-first.

    Convenience wrapper around :func:`list_timeline` with a
    single-LEA scope. The drawer panel from Session 9 calls this.
    """

    return await list_timeline(
        session_factory,
        TimelineFilter(lea_ids=frozenset({lea_id}), limit=limit),
    )


async def list_timeline(
    session_factory: async_sessionmaker[AsyncSession],
    filt: TimelineFilter,
) -> list[TimelineEntry]:
    """Return the activity timeline newest-first under the given filter.

    The cross-LEA founder explorer calls this with the filter the
    operator selected. ``lea_ids=None`` is the unrestricted scope
    (owner / admin / auditor); a non-None set
    constrains every branch's ``WHERE`` clause so an operator can
    only see LEAs in their grant set.

    Cursor pagination: the client passes the ``occurred_at`` + ``id``
    of the last entry it received as the next-page cursor. The query
    returns entries strictly older than the cursor (newest-first
    ordering means older = next page).
    """

    sql, params = _build_query(filt)
    async with session_factory() as session:
        rows = (await session.execute(text(sql), params)).all()
    return [_row_to_entry(r) for r in rows]


def _build_query(filt: TimelineFilter) -> tuple[str, dict[str, Any]]:
    """Compose the UNION query plus the outer filter WHERE clause.

    The CTE bodies stay constant. The outer ``WHERE`` block layers
    on top of the unioned shape, which keeps the branch SQL
    readable and the parameter map small. Postgres pushes the
    constant predicates back into each CTE via its planner, so the
    outer-WHERE pattern is not a performance cost at the scales
    this view targets.

    The lea-scope filter is pushed into each branch directly when
    set, because three branches do not have a top-level ``lea_id``
    column (``revert_actions`` and ``retry_actions`` only inherit
    it through ``sync_jobs``; ``audit_log`` carries its own).
    """

    params: dict[str, Any] = {"limit": filt.limit}
    if filt.lea_ids is not None:
        params["leas"] = list(filt.lea_ids)

    # CTE WHERE bits per branch. ``audit_log`` already filters out
    # NULL lea_id rows; the other branches default to ``1=1`` so the
    # lea-scope predicate is the only thing that may narrow them.
    audit_where = "WHERE al.lea_id IS NOT NULL"
    syncs_where = "WHERE 1=1"
    reverts_where = "WHERE 1=1"
    retries_where = "WHERE 1=1"
    q_created_where = "WHERE 1=1"
    q_resolved_where = "WHERE q.resolved_at IS NOT NULL"
    recon_where = "WHERE 1=1"
    if filt.lea_ids is not None:
        audit_where += " AND al.lea_id = ANY(:leas)"
        syncs_where += " AND sj.lea_id = ANY(:leas)"
        reverts_where += " AND sj.lea_id = ANY(:leas)"
        retries_where += " AND rt.lea_id = ANY(:leas)"
        q_created_where += " AND q.lea_id = ANY(:leas)"
        q_resolved_where += " AND q.lea_id = ANY(:leas)"
        recon_where += " AND rr.lea_id = ANY(:leas)"

    sql = f"""
WITH audit AS (
    SELECT
        al.id::text AS id,
        'audit_log'::text AS source,
        al.created_at AS occurred_at,
        'operator'::text AS actor_kind,
        op.email AS actor_email,
        al.action AS action,
        al.reason AS reason,
        al.target_kind AS target_kind,
        al.target_id AS target_id,
        al.detail AS detail,
        al.operator_id::text AS operator_id
    FROM audit_log al
    LEFT JOIN operator op ON op.id = al.operator_id
    {audit_where}
),
syncs AS (
    SELECT
        sj.id::text AS id,
        'sync_jobs'::text AS source,
        sj.started_at AS occurred_at,
        'system'::text AS actor_kind,
        NULL::text AS actor_email,
        ('sync.' || sj.status) AS action,
        sj.error_summary AS reason,
        'sync_job'::text AS target_kind,
        sj.id::text AS target_id,
        jsonb_build_object(
            'partner', sj.partner,
            'lea_id', sj.lea_id,
            'event_count', sj.event_count,
            'error_count', sj.error_count,
            'warning_count', sj.warning_count,
            'completed_at', sj.completed_at,
            'cursor_before', sj.cursor_before,
            'cursor_after', sj.cursor_after
        ) AS detail,
        NULL::text AS operator_id
    FROM sync_jobs sj
    {syncs_where}
),
reverts AS (
    SELECT
        ra.id::text AS id,
        'revert_actions'::text AS source,
        ra.reverted_at AS occurred_at,
        'operator'::text AS actor_kind,
        ra.operator_identity AS actor_email,
        'sync.revert'::text AS action,
        ra.reason AS reason,
        'sync_job'::text AS target_kind,
        ra.sync_job_id::text AS target_id,
        jsonb_build_object(
            'lea_id', sj.lea_id,
            'snapshots_restored', ra.snapshots_restored,
            'revert_generation_id', ra.revert_generation_id::text
        ) AS detail,
        NULL::text AS operator_id
    FROM revert_actions ra
    JOIN sync_jobs sj ON sj.id = ra.sync_job_id
    {reverts_where}
),
retries AS (
    SELECT
        rt.id::text AS id,
        'retry_actions'::text AS source,
        rt.retried_at AS occurred_at,
        'operator'::text AS actor_kind,
        rt.operator_identity AS actor_email,
        'sync.retry_requested'::text AS action,
        rt.reason AS reason,
        'sync_job'::text AS target_kind,
        rt.sync_job_id::text AS target_id,
        jsonb_build_object(
            'partner', rt.partner,
            'lea_id', rt.lea_id,
            'forced', rt.forced,
            'cursor_rewound_to', rt.cursor_rewound_to
        ) AS detail,
        NULL::text AS operator_id
    FROM retry_actions rt
    {retries_where}
),
quarantine_created AS (
    SELECT
        (q.id::text || '#created') AS id,
        'quarantine_created'::text AS source,
        q.created_at AS occurred_at,
        'system'::text AS actor_kind,
        NULL::text AS actor_email,
        'quarantine.created'::text AS action,
        q.reason AS reason,
        'quarantine_row'::text AS target_kind,
        q.id::text AS target_id,
        jsonb_build_object(
            'lea_id', q.lea_id,
            'entity_type', q.entity_type,
            'entity_id', q.entity_id,
            'sync_job_id', q.sync_job_id::text
        ) AS detail,
        NULL::text AS operator_id
    FROM quarantine q
    {q_created_where}
),
quarantine_resolved AS (
    SELECT
        (q.id::text || '#resolved') AS id,
        'quarantine_resolved'::text AS source,
        q.resolved_at AS occurred_at,
        'operator'::text AS actor_kind,
        q.resolution_operator AS actor_email,
        ('quarantine.' || COALESCE(q.resolution_status, 'resolved')) AS action,
        NULL::text AS reason,
        'quarantine_row'::text AS target_kind,
        q.id::text AS target_id,
        jsonb_build_object(
            'lea_id', q.lea_id,
            'entity_type', q.entity_type,
            'entity_id', q.entity_id,
            'resolution_status', q.resolution_status
        ) AS detail,
        NULL::text AS operator_id
    FROM quarantine q
    {q_resolved_where}
),
reconciliations AS (
    SELECT
        rr.id::text AS id,
        'reconciliation_runs'::text AS source,
        rr.started_at AS occurred_at,
        'system'::text AS actor_kind,
        NULL::text AS actor_email,
        ('reconciliation.' || rr.status) AS action,
        rr.error_message AS reason,
        'reconciliation_run'::text AS target_kind,
        rr.id::text AS target_id,
        jsonb_build_object(
            'partner', rr.partner,
            'lea_id', rr.lea_id,
            'canonical_root_hash', rr.canonical_root_hash,
            'partner_root_hash', rr.partner_root_hash,
            'completed_at', rr.completed_at,
            'drift_count', COALESCE(
                jsonb_array_length(COALESCE(rr.drift_summary, '[]'::jsonb)),
                0
            )
        ) AS detail,
        NULL::text AS operator_id
    FROM reconciliation_runs rr
    {recon_where}
)
SELECT id, source, occurred_at, actor_kind, actor_email,
       action, reason, target_kind, target_id, detail, operator_id
FROM (
    SELECT * FROM audit
    UNION ALL SELECT * FROM syncs
    UNION ALL SELECT * FROM reverts
    UNION ALL SELECT * FROM retries
    UNION ALL SELECT * FROM quarantine_created
    UNION ALL SELECT * FROM quarantine_resolved
    UNION ALL SELECT * FROM reconciliations
) AS combined
WHERE {_outer_where(filt, params)}
ORDER BY occurred_at DESC, id DESC
LIMIT :limit
"""
    return sql, params


def _outer_where(filt: TimelineFilter, params: dict[str, Any]) -> str:
    """Build the outer-WHERE expression and bind its parameters."""

    clauses: list[str] = ["TRUE"]
    if filt.operator_id is not None:
        params["op_id"] = filt.operator_id
        clauses.append("operator_id = :op_id")
    if filt.action_prefix is not None:
        params["action_prefix"] = filt.action_prefix + "%"
        clauses.append("action LIKE :action_prefix")
    if filt.since is not None:
        params["since"] = filt.since
        clauses.append("occurred_at >= :since")
    if filt.until is not None:
        params["until"] = filt.until
        clauses.append("occurred_at <= :until")
    if (
        filt.cursor_occurred_at is not None
        and filt.cursor_id is not None
    ):
        params["cur_ts"] = filt.cursor_occurred_at
        params["cur_id"] = filt.cursor_id
        # Lex-less than the cursor on (occurred_at DESC, id DESC):
        # older timestamp, or same timestamp with smaller id.
        clauses.append(
            "(occurred_at < :cur_ts"
            " OR (occurred_at = :cur_ts AND id < :cur_id))"
        )
    return " AND ".join(clauses)


def _row_to_entry(row: object) -> TimelineEntry:
    detail = getattr(row, "detail", None)
    if detail is not None and not isinstance(detail, dict):
        # SQLAlchemy returns JSONB as dict on psycopg; a defensive
        # branch covers drivers that yield text.
        import json

        detail = json.loads(detail)
    return TimelineEntry(
        id=row.id,  # type: ignore[attr-defined]
        source=row.source,  # type: ignore[attr-defined]
        occurred_at=row.occurred_at,  # type: ignore[attr-defined]
        actor_kind=row.actor_kind,  # type: ignore[attr-defined]
        actor_email=row.actor_email,  # type: ignore[attr-defined]
        action=row.action,  # type: ignore[attr-defined]
        reason=row.reason,  # type: ignore[attr-defined]
        target_kind=row.target_kind,  # type: ignore[attr-defined]
        target_id=row.target_id,  # type: ignore[attr-defined]
        detail=detail,
    )


__all__ = [
    "ActorKind",
    "TimelineEntry",
    "TimelineFilter",
    "TimelineSource",
    "list_timeline",
    "list_timeline_for_lea",
]
