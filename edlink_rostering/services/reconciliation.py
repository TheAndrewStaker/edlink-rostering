"""Per-LEA Merkle reconciliation.

Per ``docs/design/edlink-oneroster-rostering.md`` § "Reconciliation":
hash canonical rows per entity type, fold into a per-LEA root, compare
against a partner-side root computed from the EdLink resource
endpoints at point-in-time. On match, write a ``reconciliation_runs``
row with status ``matched``. On drift, write status ``drift_detected``
plus a JSON column listing divergent entity ids. An accompanying
alert fires through :class:`AlertService` so the operator dashboard
surfaces the drift the next time the alerts feed refreshes.

Quiet-window check: production runs reconciliation at 02:00 LEA-local
time and only when the LEA's cursor has been quiet for >= 60 minutes.
A poll mid-reconcile would compare canonical to a moving target. The
service exposes ``reconcile_lea(lea_id, partner_snapshot,
require_quiet_minutes=60)`` so the timer wrapper can call the same
path the tests exercise, and so a forced one-off reconcile can bypass
the quiet window with ``require_quiet_minutes=0``.

Partner-side snapshot: the production walk over EdLink's resource
endpoints (``/people``, ``/enrollments``, ...) is not in the POC's
mocked connector surface. The service takes the partner-side
projection as an injected callable so the test suite supplies a
fixture-projected state, and a future Azure Function timer wires in
the real walk without touching this module.

Hash model:

- **Leaf** = SHA-256(JSON-canonicalized row content). One leaf per
  canonical entity.
- **Mid** = SHA-256(concat of leaves sorted by natural key). One mid
  per (lea_id, entity_type).
- **Root** = SHA-256(concat of mid hashes sorted by entity type name).
  One root per LEA.

The drift summary records per-entity-type mid hash mismatches plus
the set-difference of entity ids on each side so an operator can drill
into the affected rows.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId


# Entity types this reconciliation covers. Maps the canonical entity
# name to its Postgres table and the columns that participate in the
# leaf hash. The reconciliation Merkle root folds one mid-hash per
# entry; adding a new entity-type to the canonical model is one
# additional entry here plus the matching projector in the connector's
# ``walk_resources``.
_ENTITY_TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "students": (
        "students",
        (
            "id",
            "lea_id",
            "given_name",
            "family_name",
            "grade",
            "preferred_first_name",
            "primary_school_id",
        ),
    ),
    "enrollments": (
        "enrollments",
        (
            "id",
            "lea_id",
            "student_id",
            "class_id",
            "begin_date",
            "end_date",
        ),
    ),
    "classes": (
        "classes",
        (
            "id",
            "lea_id",
            "title",
            "course_code",
            "school_id",
            "term_id",
        ),
    ),
    "academic_sessions": (
        "academic_sessions",
        (
            "id",
            "lea_id",
            "title",
            "session_type",
            "school_year",
            "start_date",
            "end_date",
        ),
    ),
    "schools": (
        "schools",
        (
            "id",
            "lea_id",
            "name",
            "school_code",
            "parent_org_id",
        ),
    ),
}


# Callable shape for the partner-side snapshot. Production wires this to
# the EdLink resource-endpoint walk; tests pass a synthetic dict so the
# diff logic is exercised without network or fixture file dependencies.
PartnerSnapshot = Callable[
    [LeaId], Awaitable[dict[str, list[dict[str, Any]]]]
]


def partner_snapshot_from_connector(connector: Any) -> PartnerSnapshot:
    """Wrap a connector's ``walk_resources`` as the partner_snapshot callable.

    The CLI ``reconcile`` command and the Azure Function timer wrap
    their concrete connector instance with this so the connector
    surface and the reconciliation surface stay decoupled. Tests can
    still pass their own callable to exercise the diff logic without
    instantiating a connector.

    A type-erased ``Any`` parameter is intentional here so this module
    does not import the connector package and create a circular
    dependency (the connectors import :class:`ReconciliationService` in
    some paths; the reverse import would close the loop). The
    structural contract is simply "has an async ``walk_resources``
    method that takes a ``LeaId`` and returns the partner-snapshot
    dict."
    """

    async def _snapshot(lea_id: LeaId) -> dict[str, list[dict[str, Any]]]:
        return await connector.walk_resources(lea_id)

    return _snapshot


@dataclass(frozen=True)
class EntityTypeHashes:
    """Mid-hash plus the natural-key set for one (lea, entity_type)."""

    mid_hash: str
    entity_ids: tuple[str, ...]


@dataclass(frozen=True)
class SideHashes:
    """One side (canonical or partner) of a reconciliation comparison."""

    root_hash: str
    by_entity_type: dict[str, EntityTypeHashes]


@dataclass(frozen=True)
class ReconciliationDriftDetail:
    """Per-entity-type drift specifics for one reconciliation run."""

    entity_type: str
    canonical_only_ids: tuple[str, ...]
    partner_only_ids: tuple[str, ...]
    canonical_mid_hash: str
    partner_mid_hash: str


@dataclass(frozen=True)
class ReconciliationReport:
    """Outcome of one reconciliation pass."""

    id: uuid.UUID
    lea_id: LeaId
    partner: str
    started_at: datetime
    completed_at: datetime
    status: str  # matched | drift_detected | skipped_quiet_window | failed
    canonical_root_hash: str
    partner_root_hash: str | None
    drift: tuple[ReconciliationDriftDetail, ...] = ()
    error_message: str | None = None


class ReconciliationService:
    """Drives the daily Merkle reconciliation per LEA."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = session_factory

    async def reconcile_lea(
        self,
        *,
        lea_id: LeaId,
        partner: str,
        partner_snapshot: PartnerSnapshot,
        require_quiet_minutes: int = 60,
    ) -> ReconciliationReport:
        started_at = datetime.now(UTC)
        run_id = uuid.uuid4()

        async with self._sessions() as session:
            # Quiet-window check first so the canonical-side scan does
            # not run when we already know the comparison would be
            # against a moving target.
            quiet = await _cursor_quiet_for(
                session, lea_id, partner, require_quiet_minutes
            )
            if not quiet:
                completed_at = datetime.now(UTC)
                canonical_hash = "skipped"
                await _insert_run(
                    session=session,
                    run_id=run_id,
                    lea_id=lea_id,
                    partner=partner,
                    started_at=started_at,
                    completed_at=completed_at,
                    status="skipped_quiet_window",
                    canonical_root_hash=canonical_hash,
                    partner_root_hash=None,
                    drift_summary=None,
                    error_message=None,
                )
                await session.commit()
                return ReconciliationReport(
                    id=run_id,
                    lea_id=lea_id,
                    partner=partner,
                    started_at=started_at,
                    completed_at=completed_at,
                    status="skipped_quiet_window",
                    canonical_root_hash=canonical_hash,
                    partner_root_hash=None,
                )

            canonical = await _compute_canonical_hashes(session, lea_id)

        partner_payload = await partner_snapshot(lea_id)
        partner_hashes = _compute_partner_hashes(partner_payload, lea_id)

        drift = _diff_sides(canonical, partner_hashes)
        status = "matched" if not drift else "drift_detected"

        completed_at = datetime.now(UTC)
        async with self._sessions() as session:
            await _insert_run(
                session=session,
                run_id=run_id,
                lea_id=lea_id,
                partner=partner,
                started_at=started_at,
                completed_at=completed_at,
                status=status,
                canonical_root_hash=canonical.root_hash,
                partner_root_hash=partner_hashes.root_hash,
                drift_summary=_drift_summary(drift) if drift else None,
                error_message=None,
            )
            await session.commit()

        return ReconciliationReport(
            id=run_id,
            lea_id=lea_id,
            partner=partner,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            canonical_root_hash=canonical.root_hash,
            partner_root_hash=partner_hashes.root_hash,
            drift=tuple(drift),
        )


# ── Hash computation ──────────────────────────────────────────────────────────


def _hash_row(values: dict[str, Any]) -> str:
    """SHA-256 over a JSON-canonicalized dict.

    Keys sort alphabetically so leaf hashes are reproducible across
    runs and across the canonical / partner sides. Dates become
    ISO-8601 strings; UUIDs stringify; None stays None.
    """

    payload = json.dumps(
        values, sort_keys=True, default=_json_default, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _mid_hash(leaves: list[tuple[str, str]]) -> tuple[str, tuple[str, ...]]:
    """Hash of a sorted concatenation of (entity_id, leaf_hash)."""

    leaves.sort(key=lambda pair: pair[0])
    ids = tuple(pair[0] for pair in leaves)
    h = hashlib.sha256()
    for entity_id, leaf in leaves:
        h.update(entity_id.encode("utf-8"))
        h.update(b"\x00")
        h.update(leaf.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest(), ids


def _root_hash(by_type: dict[str, EntityTypeHashes]) -> str:
    """Hash of mid hashes sorted by entity type name."""

    h = hashlib.sha256()
    for entity_type in sorted(by_type):
        h.update(entity_type.encode("utf-8"))
        h.update(b"\x00")
        h.update(by_type[entity_type].mid_hash.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


async def _compute_canonical_hashes(
    session: AsyncSession, lea_id: LeaId
) -> SideHashes:
    by_type: dict[str, EntityTypeHashes] = {}
    for entity_name, (table, columns) in _ENTITY_TABLES.items():
        col_list = ", ".join(columns)
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT {col_list}
                    FROM {table}
                    WHERE lea_id = :lea_id AND deleted_at IS NULL
                    """
                ),
                {"lea_id": lea_id},
            )
        ).all()
        leaves: list[tuple[str, str]] = []
        for row in rows:
            row_dict = {col: getattr(row, col) for col in columns}
            entity_id = str(row_dict["id"])
            leaves.append((entity_id, _hash_row(row_dict)))
        mid, ids = _mid_hash(leaves)
        by_type[entity_name] = EntityTypeHashes(mid_hash=mid, entity_ids=ids)
    return SideHashes(root_hash=_root_hash(by_type), by_entity_type=by_type)


def _compute_partner_hashes(
    payload: dict[str, list[dict[str, Any]]], lea_id: LeaId
) -> SideHashes:
    by_type: dict[str, EntityTypeHashes] = {}
    for entity_name, (_, columns) in _ENTITY_TABLES.items():
        entries = payload.get(entity_name, [])
        leaves: list[tuple[str, str]] = []
        for entry in entries:
            # Project the partner payload through the same column set
            # the canonical hashes. Anything the partner sends that we
            # don't store does not contribute to the hash, and vice
            # versa (missing-on-partner shows up as canonical_only_ids
            # in the diff).
            row_dict = {col: entry.get(col) for col in columns}
            # Force lea_id to the target LEA so partner payloads that
            # omit the field still produce comparable leaves.
            row_dict["lea_id"] = lea_id
            entity_id = str(row_dict["id"])
            leaves.append((entity_id, _hash_row(row_dict)))
        mid, ids = _mid_hash(leaves)
        by_type[entity_name] = EntityTypeHashes(mid_hash=mid, entity_ids=ids)
    return SideHashes(root_hash=_root_hash(by_type), by_entity_type=by_type)


# ── Diff + persistence ────────────────────────────────────────────────────────


def _diff_sides(
    canonical: SideHashes, partner: SideHashes
) -> list[ReconciliationDriftDetail]:
    drift: list[ReconciliationDriftDetail] = []
    for entity_type in sorted(canonical.by_entity_type):
        c = canonical.by_entity_type[entity_type]
        p = partner.by_entity_type.get(
            entity_type, EntityTypeHashes(mid_hash="", entity_ids=())
        )
        if c.mid_hash == p.mid_hash:
            continue
        c_ids = set(c.entity_ids)
        p_ids = set(p.entity_ids)
        drift.append(
            ReconciliationDriftDetail(
                entity_type=entity_type,
                canonical_only_ids=tuple(sorted(c_ids - p_ids)),
                partner_only_ids=tuple(sorted(p_ids - c_ids)),
                canonical_mid_hash=c.mid_hash,
                partner_mid_hash=p.mid_hash,
            )
        )
    return drift


def _drift_summary(drift: list[ReconciliationDriftDetail]) -> str:
    """JSON-serialized drift summary for the audit row."""

    return json.dumps(
        [
            {
                "entity_type": d.entity_type,
                "canonical_only_ids": list(d.canonical_only_ids),
                "partner_only_ids": list(d.partner_only_ids),
                "canonical_mid_hash": d.canonical_mid_hash,
                "partner_mid_hash": d.partner_mid_hash,
            }
            for d in drift
        ]
    )


async def _cursor_quiet_for(
    session: AsyncSession,
    lea_id: LeaId,
    partner: str,
    require_quiet_minutes: int,
) -> bool:
    if require_quiet_minutes <= 0:
        return True
    row = (
        await session.execute(
            text(
                """
                SELECT last_event_at
                FROM cursor_state
                WHERE lea_id = :lea AND partner = :partner
                """
            ),
            {"lea": lea_id, "partner": partner},
        )
    ).first()
    if row is None or row.last_event_at is None:
        # No cursor yet means no recent events; reconciliation can
        # proceed against the (empty or seeded) canonical state.
        return True
    now = datetime.now(UTC)
    elapsed = (now - row.last_event_at).total_seconds() / 60.0
    return bool(elapsed >= require_quiet_minutes)


async def _insert_run(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    lea_id: LeaId,
    partner: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    canonical_root_hash: str,
    partner_root_hash: str | None,
    drift_summary: str | None,
    error_message: str | None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO reconciliation_runs (
                id, lea_id, partner, started_at, completed_at,
                status, canonical_root_hash, partner_root_hash,
                drift_summary, error_message
            ) VALUES (
                :id, :lea, :partner, :started, :completed,
                :status, :canonical, :partner_hash,
                CAST(:drift AS JSONB), :error
            )
            """
        ),
        {
            "id": run_id,
            "lea": lea_id,
            "partner": partner,
            "started": started_at,
            "completed": completed_at,
            "status": status,
            "canonical": canonical_root_hash,
            "partner_hash": partner_root_hash,
            "drift": drift_summary,
            "error": error_message,
        },
    )


__all__ = [
    "EntityTypeHashes",
    "PartnerSnapshot",
    "ReconciliationDriftDetail",
    "ReconciliationReport",
    "ReconciliationService",
    "SideHashes",
    "partner_snapshot_from_connector",
]
