"""Quarantine queue read + action endpoints.

Domain exceptions raised by the service (``QuarantineNotFound``,
``QuarantineAlreadyResolved``, ``QuarantineRefused``) are mapped to
RFC 7807 ProblemDetail responses by the global handler registered in
:mod:`edlink_rostering.api.errors`. Routers just ``raise``.

Mutation endpoints accept an optional ``Idempotency-Key`` header.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api._lookups import assert_authorized, load_quarantine_lea
from edlink_rostering.api.auth import Operator, require
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.schemas import (
    QuarantineRejectRequest,
    QuarantineRejectResponse,
    QuarantineReleaseResponse,
    QuarantineRowOut,
)
from edlink_rostering.core.types import LeaId
from edlink_rostering.services.idempotency import with_idempotency
from edlink_rostering.services.quarantine import QuarantineService

router = APIRouter(prefix="/quarantine", tags=["quarantine"])


@router.get(
    "",
    response_model=list[QuarantineRowOut],
    operation_id="quarantine.list",
)
async def list_quarantine(
    lea_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    op: Operator = Depends(require("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[QuarantineRowOut]:
    """Unresolved quarantine rows for the admin queue.

    Role gate: ``auditor``. The ``operator`` role sees only LEAs in
    its grant set per V0005; the other roles see every quarantine
    row. ``lea_id`` (request-supplied query) composes with the
    operator-scope filter; an operator passing ``lea_id`` for an LEA
    outside their grant gets an empty list.
    """

    scope: frozenset[LeaId] | None
    if op.role == "operator":
        scope = op.authorized_leas
        if not scope:
            return []
    else:
        scope = None

    service = QuarantineService(session_factory=factory)
    rows = await service.list_unresolved(
        lea_id=LeaId(lea_id) if lea_id else None,
        authorized_leas=scope,
        limit=limit,
    )
    return [
        QuarantineRowOut(
            id=r.id,
            sync_job_id=r.sync_job_id,
            lea_id=str(r.lea_id),
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            reason=r.reason,
            created_at=r.created_at,
            resolved_at=r.resolved_at,
            resolution_status=r.resolution_status,
            resolution_operator=r.resolution_operator,
        )
        for r in rows
    ]


@router.post(
    "/{quarantine_id}/release",
    response_model=QuarantineReleaseResponse,
    operation_id="quarantine.release",
)
async def release_quarantine(
    quarantine_id: uuid.UUID,
    request: Request,
    op: Operator = Depends(require("operator")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> QuarantineReleaseResponse:
    async with factory() as session:
        target_lea = await load_quarantine_lea(session, quarantine_id)
    assert_authorized(op.authorized_leas, target_lea)

    async def _work() -> QuarantineReleaseResponse:
        service = QuarantineService(session_factory=factory)
        outcome = await service.release(
            quarantine_id=quarantine_id,
            operator_identity=op.subject,
        )
        return QuarantineReleaseResponse(
            quarantine_id=outcome.quarantine_id,
            release_generation_id=outcome.release_generation_id,
            entity_type=outcome.entity_type,
            entity_id=outcome.entity_id,
        )

    return await with_idempotency(
        factory=factory,
        operator_id=op.id,
        route="quarantine.release",
        path=request.url.path,
        idempotency_key=idempotency_key,
        request_body=None,
        response_model=QuarantineReleaseResponse,
        handler=_work,
    )


@router.post(
    "/{quarantine_id}/reject",
    response_model=QuarantineRejectResponse,
    operation_id="quarantine.reject",
)
async def reject_quarantine(
    quarantine_id: uuid.UUID,
    body: QuarantineRejectRequest,
    request: Request,
    op: Operator = Depends(require("operator")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> QuarantineRejectResponse:
    async with factory() as session:
        target_lea = await load_quarantine_lea(session, quarantine_id)
    assert_authorized(op.authorized_leas, target_lea)

    async def _work() -> QuarantineRejectResponse:
        service = QuarantineService(session_factory=factory)
        outcome = await service.reject(
            quarantine_id=quarantine_id,
            operator_identity=op.subject,
            reason=body.reason,
        )
        return QuarantineRejectResponse(
            quarantine_id=outcome.quarantine_id,
            rejected_at=outcome.rejected_at,
        )

    return await with_idempotency(
        factory=factory,
        operator_id=op.id,
        route="quarantine.reject",
        path=request.url.path,
        idempotency_key=idempotency_key,
        request_body=body,
        response_model=QuarantineRejectResponse,
        handler=_work,
    )
