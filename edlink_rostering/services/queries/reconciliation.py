"""Query module for the reconciliation aggregate.

One read function backs ``/api/leas/{lea_id}/reconciliation``. The
router maps the typed dataclasses below to Pydantic response models;
no SQL lives in the router.

The JSONB ``drift_summary`` column is unpacked into typed
:class:`DriftDetailRow` dataclasses here so the router does not have
to reach into raw JSON.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class DriftDetailRow:
    """One per-entity-type drift entry inside a reconciliation_runs row."""

    entity_type: str
    canonical_only_ids: list[str]
    partner_only_ids: list[str]
    canonical_mid_hash: str
    partner_mid_hash: str


@dataclass(frozen=True)
class ReconciliationRunRow:
    """One ``reconciliation_runs`` row in the shape the API surfaces."""

    id: uuid.UUID
    lea_id: str
    partner: str
    started_at: datetime
    completed_at: datetime
    status: str
    canonical_root_hash: str
    partner_root_hash: str | None
    drift: list[DriftDetailRow] = field(default_factory=list)
    error_message: str | None = None


_LIST_SQL = text(
    """
    SELECT id, lea_id, partner, started_at, completed_at,
           status, canonical_root_hash, partner_root_hash,
           drift_summary, error_message
    FROM reconciliation_runs
    WHERE lea_id = :lea AND partner = :partner
    ORDER BY started_at DESC
    LIMIT :limit
    """
)


def _to_drift(entries: Any) -> list[DriftDetailRow]:
    if not entries:
        return []
    out: list[DriftDetailRow] = []
    for d in entries:
        out.append(
            DriftDetailRow(
                entity_type=str(d.get("entity_type", "")),
                canonical_only_ids=list(d.get("canonical_only_ids") or []),
                partner_only_ids=list(d.get("partner_only_ids") or []),
                canonical_mid_hash=str(d.get("canonical_mid_hash", "")),
                partner_mid_hash=str(d.get("partner_mid_hash", "")),
            )
        )
    return out


def _to_run_row(row: Any) -> ReconciliationRunRow:
    return ReconciliationRunRow(
        id=row.id,
        lea_id=row.lea_id,
        partner=row.partner,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        canonical_root_hash=row.canonical_root_hash,
        partner_root_hash=row.partner_root_hash,
        drift=_to_drift(row.drift_summary),
        error_message=row.error_message,
    )


async def list_reconciliation_runs_for_lea(
    factory: async_sessionmaker[AsyncSession],
    *,
    lea_id: str,
    partner: str,
    limit: int,
) -> list[ReconciliationRunRow]:
    """Recent reconciliation runs for one LEA + partner, newest first."""

    async with factory() as session:
        rows = (
            await session.execute(
                _LIST_SQL,
                {"lea": lea_id, "partner": partner, "limit": limit},
            )
        ).all()
    return [_to_run_row(r) for r in rows]


__all__ = [
    "DriftDetailRow",
    "ReconciliationRunRow",
    "list_reconciliation_runs_for_lea",
]
