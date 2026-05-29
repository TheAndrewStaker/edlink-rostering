"""Connector authorization endpoints.

The Phase 1.5d surface from ``docs/design/admin-surfaces.md``: list +
three lifecycle verbs (authorize, revoke, adjust-poll-interval).
Mutations are gated to ``admin`` per the role matrix; the list is open
to ``auditor`` (and therefore operator + admin + owner).

Multi-tenancy on the list endpoint: the ``operator`` role is scoped to
its ``authorized_leas`` set, and the list query filters by that set so
an LEA the operator is not authorized for never appears. Founder_admin,
admin, and auditor get the implicit organization-wide set,
which is treated as "no scope filter" inside the service layer.

Every mutation writes an ``audit_log`` row in the same transaction as
the canonical change. The audit-log explorer in Phase 2 will UNION
these with the sync-side audit tables.

Domain exceptions (``ConnectorAuthorizationNotFound``) are mapped to
RFC 7807 ProblemDetail responses by the global handler registered in
:mod:`edlink_rostering.api.errors`. Routers just ``raise``.

Mutation endpoints accept an optional ``Idempotency-Key`` header.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.auth import Operator, require
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.schemas import (
    ConnectorAdjustPollIntervalRequest,
    ConnectorAdjustPollIntervalResponse,
    ConnectorAuthorizationOut,
    ConnectorAuthorizeRequest,
    ConnectorAuthorizeResponse,
    ConnectorRevokeRequest,
    ConnectorRevokeResponse,
)
from edlink_rostering.core.types import LeaId
from edlink_rostering.services.connector_authz import ConnectorAuthorizationService
from edlink_rostering.services.idempotency import with_idempotency


router = APIRouter(prefix="/connectors", tags=["connectors"])


def _service(
    factory: async_sessionmaker[AsyncSession],
) -> ConnectorAuthorizationService:
    return ConnectorAuthorizationService(session_factory=factory)


def _row_to_out(row: object) -> ConnectorAuthorizationOut:
    return ConnectorAuthorizationOut.model_validate(row, from_attributes=True)


@router.get(
    "",
    response_model=list[ConnectorAuthorizationOut],
    operation_id="connectors.list",
)
async def list_connectors(
    op: Operator = Depends(require("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    lea_id: str | None = Query(default=None),
    include_revoked: bool = Query(default=False),
) -> list[ConnectorAuthorizationOut]:
    """Return integration authorization rows.

    The ``operator`` role sees only LEAs it has been granted scope for;
    ``admin``, ``owner``, and ``auditor`` see every
    LEA. The role gate already excluded anonymous callers; the scope
    filter here is the per-LEA layer on top.

    Optional query params:

    - ``lea_id``: narrow to a single LEA. Used by the LEA detail
      drawer's Integration section so it loads only the rows it
      renders without a separate endpoint.
    - ``include_revoked``: include rows where ``revoked_at`` is set.
      Used by the Integrations page's "Include revoked" toggle to
      surface revocation history without a separate audit query.
      Live rows are returned first; revoked rows follow most-recent
      first.
    """

    scope: frozenset[LeaId] | None
    if op.role == "operator":
        scope = op.authorized_leas
    else:
        scope = None

    rows = await _service(factory).list_authorizations(
        scope,
        lea_id=LeaId(lea_id) if lea_id else None,
        include_revoked=include_revoked,
    )
    return [_row_to_out(r) for r in rows]


@router.post(
    "/{lea_id}/{partner}/authorize",
    response_model=ConnectorAuthorizeResponse,
    operation_id="connectors.authorize",
)
async def authorize_connector(
    lea_id: str,
    partner: str,
    body: ConnectorAuthorizeRequest,
    request: Request,
    op: Operator = Depends(require("admin")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ConnectorAuthorizeResponse:
    async def _work() -> ConnectorAuthorizeResponse:
        outcome = await _service(factory).authorize(
            lea_id=LeaId(lea_id),
            partner=partner,
            operator_id=op.id,
            reason=body.reason,
            poll_interval_seconds=body.poll_interval_seconds,
            notes=body.notes,
        )
        return ConnectorAuthorizeResponse(
            id=outcome.id,
            lea_id=outcome.lea_id,
            partner=outcome.partner,
            status=outcome.status,
            poll_interval_seconds=outcome.poll_interval_seconds,
            created_new_row=outcome.created_new_row,
        )

    return await with_idempotency(
        factory=factory,
        operator_id=op.id,
        route="connectors.authorize",
        path=request.url.path,
        idempotency_key=idempotency_key,
        request_body=body,
        response_model=ConnectorAuthorizeResponse,
        handler=_work,
    )


@router.post(
    "/{lea_id}/{partner}/revoke",
    response_model=ConnectorRevokeResponse,
    operation_id="connectors.revoke",
)
async def revoke_connector(
    lea_id: str,
    partner: str,
    body: ConnectorRevokeRequest,
    request: Request,
    op: Operator = Depends(require("admin")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ConnectorRevokeResponse:
    async def _work() -> ConnectorRevokeResponse:
        outcome = await _service(factory).revoke(
            lea_id=LeaId(lea_id),
            partner=partner,
            operator_id=op.id,
            reason=body.reason,
        )
        return ConnectorRevokeResponse(
            id=outcome.id,
            lea_id=outcome.lea_id,
            partner=outcome.partner,
            revoked_at=outcome.revoked_at,
        )

    return await with_idempotency(
        factory=factory,
        operator_id=op.id,
        route="connectors.revoke",
        path=request.url.path,
        idempotency_key=idempotency_key,
        request_body=body,
        response_model=ConnectorRevokeResponse,
        handler=_work,
    )


@router.post(
    "/{lea_id}/{partner}/adjust-poll-interval",
    response_model=ConnectorAdjustPollIntervalResponse,
    operation_id="connectors.adjust_poll_interval",
)
async def adjust_poll_interval(
    lea_id: str,
    partner: str,
    body: ConnectorAdjustPollIntervalRequest,
    request: Request,
    op: Operator = Depends(require("admin")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ConnectorAdjustPollIntervalResponse:
    async def _work() -> ConnectorAdjustPollIntervalResponse:
        outcome = await _service(factory).adjust_poll_interval(
            lea_id=LeaId(lea_id),
            partner=partner,
            new_poll_interval_seconds=body.new_poll_interval_seconds,
            operator_id=op.id,
            reason=body.reason,
        )
        return ConnectorAdjustPollIntervalResponse(
            id=outcome.id,
            lea_id=outcome.lea_id,
            partner=outcome.partner,
            previous_poll_interval_seconds=outcome.previous_poll_interval_seconds,
            new_poll_interval_seconds=outcome.new_poll_interval_seconds,
        )

    return await with_idempotency(
        factory=factory,
        operator_id=op.id,
        route="connectors.adjust_poll_interval",
        path=request.url.path,
        idempotency_key=idempotency_key,
        request_body=body,
        response_model=ConnectorAdjustPollIntervalResponse,
        handler=_work,
    )
