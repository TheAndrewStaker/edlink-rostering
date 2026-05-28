"""Dev-only "Send test event" endpoint.

The admin app's per-LEA drawer surfaces a menu of nine well-known
scenarios (happy path, Layers 1-5, reconciliation drift) the operator
fires during a live walkthrough. This router exposes the catalog and
the dispatch action behind the same dev-profile gate as the JWT
minter, plus a `require("admin")` check so the auditor
persona switcher cannot trigger writes.

The handler is intentionally thin: it loads the catalog, validates
the scenario id, and forwards to the dispatcher. Side effects land
in the same Postgres tables a real sync would touch, so the rest of
the admin app does not need a parallel "test event" pane.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.auth import Operator, require
from edlink_rostering.api.dependencies import get_session_factory
from edlink_rostering.api.routers.dev import is_dev_profile
from edlink_rostering.core.types import LeaId
from edlink_rostering.services.test_events import TestEventService


router = APIRouter(prefix="/dev/test-events", tags=["dev"])


class ScenarioOut(BaseModel):
    id: str
    label: str
    section: str
    kind: str
    description: str


class CatalogResponse(BaseModel):
    scenarios: list[ScenarioOut]


class DispatchRequest(BaseModel):
    lea_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)


class DispatchResponse(BaseModel):
    sync_job_id: str
    scenario_id: str
    lea_id: str
    running_visibility_seconds: float


def _fixtures_dir() -> Path:
    """Resolve the test-events fixture directory at request time.

    Lazy resolution lets tests rebind the prototype root via the
    `PROTOTYPE_ROOT` env var without monkey-patching this module.
    Production reads the bundle on disk.
    """

    return (
        Path(__file__).resolve().parents[3]
        / "fixtures"
        / "edlink"
        / "test-events"
    )


def _require_dev() -> None:
    if not is_dev_profile():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found.",
        )


def _service(
    factory: async_sessionmaker[AsyncSession],
) -> TestEventService:
    return TestEventService(
        session_factory=factory,
        fixtures_dir=_fixtures_dir(),
    )


@router.get(
    "/scenarios",
    response_model=CatalogResponse,
    operation_id="dev_test_events.list_scenarios",
)
async def list_scenarios(
    op: Operator = Depends(require("admin")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> CatalogResponse:
    """Return the catalog the React menu renders.

    Returns 404 outside dev to keep the route from leaking on a
    misconfigured prod. The `admin` gate keeps the auditor
    persona from seeing a write-only surface.
    """

    _require_dev()
    scenarios = _service(factory).list_scenarios()
    return CatalogResponse(
        scenarios=[
            ScenarioOut(
                id=s.id,
                label=s.label,
                section=s.section,
                kind=s.kind,
                description=s.description,
            )
            for s in scenarios
        ]
    )


@router.post(
    "",
    response_model=DispatchResponse,
    operation_id="dev_test_events.dispatch",
)
async def dispatch(
    body: DispatchRequest,
    op: Operator = Depends(require("admin")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> DispatchResponse:
    """Enqueue one scenario at the named LEA.

    The endpoint returns as soon as the running sync_jobs row is
    committed; the side-effect handler runs in the background after a
    visibility delay so React Query's poll cycle catches the running
    state. Operators see a toast confirming receipt; the per-LEA pill
    and the dashboard's `in_flight` count light up on the next poll.

    ``ScenarioNotFound`` raised by the service is mapped to a 404
    ProblemDetail by the global error handler.
    """

    _require_dev()
    if body.lea_id not in op.authorized_leas:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Operator {op.subject!r} is not authorized for LEA "
                f"{body.lea_id!r}."
            ),
        )
    service = _service(factory)
    outcome = await service.enqueue(
        lea_id=LeaId(body.lea_id),
        scenario_id=body.scenario_id,
        operator_subject=op.subject,
    )
    return DispatchResponse(
        sync_job_id=str(outcome.sync_job_id),
        scenario_id=outcome.scenario_id,
        lea_id=outcome.lea_id,
        running_visibility_seconds=outcome.running_visibility_seconds,
    )


__all__ = ["router"]
