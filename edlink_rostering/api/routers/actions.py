"""Operator action endpoints: retry, revert.

Each endpoint wraps the matching service class so the CLI and the HTTP
surface execute identical business logic. The ``require("operator")``
dependency replaces the Phase 1 mock header and resolves the audit
row's actor from the validated JWT subject.

Domain exceptions raised by the service classes (``RetrySyncJobNotFound``,
``RetryRefused``, ``RevertSyncJobNotFound``, ``RevertRefused``) are
mapped to RFC 7807 ProblemDetail responses by the global handler
registered in :mod:`edlink_rostering.api.errors`. Routers just ``raise``.

Optional ``Idempotency-Key`` header: when supplied, the response is
cached for 24h and a re-request with the same key returns the cached
response instead of executing the mutation again. See
:mod:`edlink_rostering.services.idempotency`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api._lookups import assert_authorized, load_sync_job_lea
from edlink_rostering.api.auth import Operator, require
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.schemas import (
    RetryRequest,
    RetryResponse,
    RevertRequest,
    RevertResponse,
)
from edlink_rostering.services.idempotency import with_idempotency
from edlink_rostering.services.retry import RetryService
from edlink_rostering.services.revert import RevertService

router = APIRouter(prefix="/syncs", tags=["actions"])


@router.post(
    "/{sync_job_id}/retry",
    response_model=RetryResponse,
    operation_id="actions.retry_sync",
)
async def retry_sync(
    sync_job_id: uuid.UUID,
    body: RetryRequest,
    request: Request,
    op: Operator = Depends(require("operator")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RetryResponse:
    async with factory() as session:
        target_lea = await load_sync_job_lea(session, sync_job_id)
    assert_authorized(op.authorized_leas, target_lea)

    async def _work() -> RetryResponse:
        service = RetryService(session_factory=factory)
        outcome = await service.retry(
            sync_job_id=sync_job_id,
            operator_identity=op.subject,
            reason=body.reason,
            forced=body.forced,
        )
        return RetryResponse(
            sync_job_id=outcome.sync_job_id,
            lea_id=str(outcome.lea_id),
            partner=outcome.partner,
            cursor_rewound_to=outcome.cursor_rewound_to,
            forced=outcome.forced,
        )

    return await with_idempotency(
        factory=factory,
        operator_id=op.id,
        route="actions.retry_sync",
        path=request.url.path,
        idempotency_key=idempotency_key,
        request_body=body,
        response_model=RetryResponse,
        handler=_work,
    )


@router.post(
    "/{sync_job_id}/revert",
    response_model=RevertResponse,
    operation_id="actions.revert_sync",
)
async def revert_sync(
    sync_job_id: uuid.UUID,
    body: RevertRequest,
    request: Request,
    op: Operator = Depends(require("operator")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RevertResponse:
    async with factory() as session:
        target_lea = await load_sync_job_lea(session, sync_job_id)
    assert_authorized(op.authorized_leas, target_lea)

    async def _work() -> RevertResponse:
        service = RevertService(session_factory=factory)
        outcome = await service.revert(
            sync_job_id=sync_job_id,
            operator_identity=op.subject,
            reason=body.reason,
        )
        return RevertResponse(
            sync_job_id=outcome.sync_job_id,
            revert_generation_id=outcome.revert_generation_id,
            snapshots_restored=outcome.snapshots_restored,
            canonical_rows_updated=outcome.canonical_rows_updated,
            canonical_rows_soft_deleted=outcome.canonical_rows_soft_deleted,
        )

    return await with_idempotency(
        factory=factory,
        operator_id=op.id,
        route="actions.revert_sync",
        path=request.url.path,
        idempotency_key=idempotency_key,
        request_body=body,
        response_model=RevertResponse,
        handler=_work,
    )
