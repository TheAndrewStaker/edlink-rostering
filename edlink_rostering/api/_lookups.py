"""Per-action ``lea_id`` lookups for the authz layer.

The multi-tenancy enforcement on state-mutating endpoints needs to
know the target row's ``lea_id`` BEFORE the action service runs.
These helpers load just the ``lea_id`` for each addressable resource
and translate "row not found" into a 404 (NOT a 403, since a 403 on
an unknown id is information disclosure: it confirms the id exists
in another LEA).

Each helper takes the request's resource id and returns a
``LeaId``. Callers compare against ``op.authorized_leas`` and raise
403 themselves when the operator is not scoped to the target.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edlink_rostering.core.types import LeaId


async def load_sync_job_lea(
    session: AsyncSession, sync_job_id: uuid.UUID
) -> LeaId:
    """Return the LEA that owns this sync_job.

    A revert writes a synthetic sync_job of ``status='revert'`` whose
    ``lea_id`` is the same as the parent sync; the lookup does not
    care about the status, only the LEA. Raises 404 when the id is
    unknown.
    """

    row = (
        await session.execute(
            text("SELECT lea_id FROM sync_jobs WHERE id = :id"),
            {"id": sync_job_id},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"sync_job {sync_job_id} not found.",
        )
    return LeaId(row.lea_id)


async def load_quarantine_lea(
    session: AsyncSession, quarantine_id: uuid.UUID
) -> LeaId:
    """Return the LEA that owns this quarantine row.

    Raises 404 when the id is unknown so an operator scoped to a
    different LEA cannot infer that the id is real.
    """

    row = (
        await session.execute(
            text("SELECT lea_id FROM quarantine WHERE id = :id"),
            {"id": quarantine_id},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"quarantine row {quarantine_id} not found.",
        )
    return LeaId(row.lea_id)


def assert_authorized(op_authorized: frozenset[LeaId], target: LeaId) -> None:
    """Raise 403 if the operator is not scoped to the target LEA.

    The lookup helpers above already raised 404 for unknown ids by
    the time this is called, so a 403 here is the correct response
    for "id exists, but not in your scope."
    """

    if target not in op_authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Operator is not authorized for LEA {target!r}."
            ),
        )


__all__ = [
    "assert_authorized",
    "load_quarantine_lea",
    "load_sync_job_lea",
]
