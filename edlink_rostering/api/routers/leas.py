"""LEA roll-up + onboarding endpoints.

Three endpoints back the LEA admin surface:

* ``GET /api/v1/leas`` returns a summary row per LEA with student /
  enrollment counts, latest sync status, cursor lag, and the new
  ``status`` column from V0009. This is the top-level table on the
  admin dashboard.
* ``POST /api/v1/leas`` creates a new LEA in ``onboarding`` status.
  The onboarding CLI (``onboard-lea``) drives this flow; the
  endpoint is also reachable directly by ``admin`` operators.
* ``PATCH /api/v1/leas/{lea_id}/status`` transitions an LEA between
  the three allowed states (``onboarding`` -> ``active``,
  ``active`` -> ``decommissioned``, ``onboarding`` -> ``decommissioned``).
  Invalid transitions surface as 422 ProblemDetails.

Read SQL lives in :mod:`edlink_rostering.services.queries.leas`. Mutation
work lives in :class:`edlink_rostering.services.lea_admin.LeaAdminService`
so the audit-log row writes inside the same transaction as the canonical
change. Domain exceptions land on the global RFC 7807 handler registered
in :mod:`edlink_rostering.api.app`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.auth import Operator, require
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.schemas import (
    LeaCreateRequest,
    LeaCreateResponse,
    LeaStatusTransitionRequest,
    LeaStatusTransitionResponse,
    LeaSummary,
)
from edlink_rostering.core.types import LeaId
from edlink_rostering.services.lea_admin import (
    CreateLeaInput,
    InvalidStatusTransition,
    LeaAdminService,
    LeaAlreadyExists,
    LeaNotFound,
    LeaStatus,
)
from edlink_rostering.services.queries.leas import (
    LeaSummaryRow,
    list_leas,
)

router = APIRouter(tags=["leas"])


_ALLOWED_TARGET_STATUSES: frozenset[str] = frozenset(
    {"onboarding", "active", "decommissioned"}
)


def _to_out(row: LeaSummaryRow) -> LeaSummary:
    return LeaSummary(
        id=row.id,
        name=row.name,
        lea_type=row.lea_type,
        state=row.state,
        status=row.status,
        student_count=row.student_count,
        enrollment_count=row.enrollment_count,
        latest_sync_at=row.latest_sync_at,
        latest_sync_status=row.latest_sync_status,
        cursor_lag_days=row.cursor_lag_days,
        in_flight_count=row.in_flight_count,
    )


@router.get("/leas", response_model=list[LeaSummary], operation_id="leas.list")
async def list_leas_route(
    op: Operator = Depends(require("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[LeaSummary]:
    """All known LEAs with roll-up metrics for the dashboard table.

    Role gate: ``auditor``. The ``operator`` role sees only LEAs in
    its grant set per V0005's ``operator_lea_grant``;
    ``admin``, ``owner``, and ``auditor`` see every
    LEA. Mirrors the scope pattern from
    :func:`edlink_rostering.services.connector_authz.list_authorizations` and
    :mod:`edlink_rostering.api.routers.audit`.
    """

    scope: frozenset[LeaId] | None
    if op.role == "operator":
        scope = op.authorized_leas
        # Defensive: a future role mapping might leave authorized_leas
        # empty for an unmapped operator. Refuse to broaden to "all
        # LEAs" silently; an empty grant set returns an empty list.
        if not scope:
            return []
    else:
        scope = None

    rows = await list_leas(factory, authorized_leas=scope)
    return [_to_out(r) for r in rows]


@router.post(
    "/leas",
    response_model=LeaCreateResponse,
    status_code=http_status.HTTP_201_CREATED,
    operation_id="leas.create",
)
async def create_lea_route(
    body: LeaCreateRequest,
    op: Operator = Depends(require("admin")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> LeaCreateResponse:
    """Create a new LEA in ``onboarding`` status.

    Role gate: ``admin``. Uniqueness violations on ``id``,
    ``name``, or ``edlink_integration_id`` surface as 409 conflicts.
    """

    service = LeaAdminService(session_factory=factory)
    try:
        row = await service.create_lea(
            params=CreateLeaInput(
                id=LeaId(body.id),
                name=body.name,
                lea_type=body.lea_type,
                state=body.state,
                timezone=body.timezone,
                nces_lea_id=body.nces_lea_id,
                edlink_integration_id=body.edlink_integration_id,
            ),
            operator_id=op.id,
        )
    except LeaAlreadyExists as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None

    return LeaCreateResponse(
        id=row.id,
        name=row.name,
        lea_type=row.lea_type,
        state=row.state,
        timezone=row.timezone,
        nces_lea_id=row.nces_lea_id,
        edlink_integration_id=row.edlink_integration_id,
        status=row.status,
    )


@router.patch(
    "/leas/{lea_id}/status",
    response_model=LeaStatusTransitionResponse,
    operation_id="leas.transition_status",
)
async def transition_lea_status_route(
    lea_id: str,
    body: LeaStatusTransitionRequest,
    op: Operator = Depends(require("admin")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> LeaStatusTransitionResponse:
    """Transition an LEA between onboarding-lifecycle states.

    Valid transitions: ``onboarding -> active``,
    ``active -> decommissioned``, ``onboarding -> decommissioned``.
    Anything else is a 422.
    """

    if body.status not in _ALLOWED_TARGET_STATUSES:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown target status {body.status!r}. Expected one"
                f" of {sorted(_ALLOWED_TARGET_STATUSES)}."
            ),
        )

    service = LeaAdminService(session_factory=factory)
    current = await service.get(LeaId(lea_id))
    if current is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"LEA {lea_id!r} not found.",
        )

    target_status: LeaStatus = body.status  # type: ignore[assignment]
    try:
        row = await service.transition_status(
            lea_id=LeaId(lea_id),
            target_status=target_status,
            operator_id=op.id,
            reason=body.reason,
        )
    except InvalidStatusTransition as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None
    except LeaNotFound as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from None

    return LeaStatusTransitionResponse(
        id=row.id,
        status=row.status,
        previous_status=current.status,
    )
