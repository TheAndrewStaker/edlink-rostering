"""LEA onboarding and lifecycle service.

Two operations back the Phase 1.5e onboarding surface:

- :meth:`create_lea` inserts a new LEA in ``onboarding`` status. The
  CLI ``onboard-lea`` flow drives this, and the POST /api/v1/leas
  endpoint exposes the same call to founder-admin operators. Uniqueness
  is enforced on ``name`` and ``edlink_integration_id`` to prevent two
  districts from sharing either label.

- :meth:`transition_status` graduates an LEA between the three states
  the onboarding model allows. The transition table is intentionally
  narrow: ``onboarding -> active`` after the first successful sync,
  ``active -> decommissioned`` when the partnership ends, and
  ``onboarding -> decommissioned`` for the abandoned-onboarding case.
  Anything else surfaces an :class:`InvalidStatusTransition` that the
  router maps to a 422 ProblemDetail.

Both methods write an ``audit_log`` row in the same transaction as the
canonical change, mirroring the pattern from
:class:`edlink_rostering.services.connector_authz.ConnectorAuthorizationService`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId


LeaStatus = Literal["onboarding", "active", "decommissioned"]


_VALID_TRANSITIONS: frozenset[tuple[LeaStatus, LeaStatus]] = frozenset(
    {
        ("onboarding", "active"),
        ("onboarding", "decommissioned"),
        ("active", "decommissioned"),
    }
)


class LeaAdminError(Exception):
    """Base for LEA admin service errors."""


class LeaAlreadyExists(LeaAdminError):
    """Raised when ``name`` or ``edlink_integration_id`` is taken."""


class LeaNotFound(LeaAdminError):
    """Raised when ``transition_status`` is asked for a missing LEA."""


class InvalidStatusTransition(LeaAdminError):
    """Raised when the requested ``(from_status, to_status)`` pair is rejected."""

    def __init__(self, current: str, requested: str) -> None:
        super().__init__(
            f"Cannot transition LEA from {current!r} to {requested!r}."
        )
        self.current = current
        self.requested = requested


@dataclass(frozen=True)
class CreateLeaInput:
    """Parameters for :meth:`LeaAdminService.create_lea`."""

    id: LeaId
    name: str
    lea_type: str
    state: str
    timezone: str
    nces_lea_id: str | None
    edlink_integration_id: str | None


@dataclass(frozen=True)
class LeaRow:
    """One ``leas`` row as returned by the admin service."""

    id: LeaId
    name: str
    lea_type: str
    state: str
    timezone: str
    nces_lea_id: str | None
    edlink_integration_id: str | None
    status: LeaStatus


class LeaAdminService:
    """Onboarding flow + status-transition flow for LEAs.

    Both methods are transactional and audit-logged. The session
    factory is identical to the pattern used by the connector authz
    and retry services.
    """

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._sessions = session_factory

    async def create_lea(
        self,
        *,
        params: CreateLeaInput,
        operator_id: uuid.UUID,
        reason: str = "lea.created via onboarding flow",
    ) -> LeaRow:
        """Insert an LEA in ``onboarding`` status.

        Uniqueness violations on ``name`` or ``edlink_integration_id``
        surface as :class:`LeaAlreadyExists`. The audit row carries
        the new LEA's id and the integration id in ``detail`` so the
        onboarding history is queryable.
        """

        async with self._sessions() as session:
            await self._assert_unique(session, params)

            now = datetime.now(UTC)
            await session.execute(
                text(
                    """
                    INSERT INTO leas (
                        id, name, lea_type, state, timezone,
                        nces_lea_id, edlink_integration_id, status
                    ) VALUES (
                        :id, :name, :lea_type, :state, :tz,
                        :nces, :integration, 'onboarding'
                    )
                    """
                ),
                {
                    "id": params.id,
                    "name": params.name,
                    "lea_type": params.lea_type,
                    "state": params.state,
                    "tz": params.timezone,
                    "nces": params.nces_lea_id,
                    "integration": params.edlink_integration_id,
                },
            )

            await _write_audit_log(
                session=session,
                operator_id=operator_id,
                action="lea.created",
                target_kind="lea",
                target_id=str(params.id),
                lea_id=params.id,
                reason=reason,
                detail={
                    "name": params.name,
                    "lea_type": params.lea_type,
                    "state": params.state,
                    "timezone": params.timezone,
                    "nces_lea_id": params.nces_lea_id,
                    "edlink_integration_id": params.edlink_integration_id,
                },
                created_at=now,
            )

            await session.commit()

        return LeaRow(
            id=params.id,
            name=params.name,
            lea_type=params.lea_type,
            state=params.state,
            timezone=params.timezone,
            nces_lea_id=params.nces_lea_id,
            edlink_integration_id=params.edlink_integration_id,
            status="onboarding",
        )

    async def transition_status(
        self,
        *,
        lea_id: LeaId,
        target_status: LeaStatus,
        operator_id: uuid.UUID,
        reason: str,
    ) -> LeaRow:
        """Move an LEA between the three allowed onboarding states.

        Invalid transitions raise :class:`InvalidStatusTransition`.
        Missing LEAs raise :class:`LeaNotFound`. The audit row carries
        both the previous and new status so the operator history is
        readable without joining back to the timeline.
        """

        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, name, lea_type, state, timezone,"
                        " nces_lea_id, edlink_integration_id, status"
                        " FROM leas WHERE id = :id AND deleted_at IS NULL"
                    ),
                    {"id": lea_id},
                )
            ).first()
            if row is None:
                raise LeaNotFound(f"LEA {lea_id!r} not found.")

            current_status: LeaStatus = row.status
            if current_status == target_status:
                # No-op transitions are still rejected so an explicit
                # transition message in the audit log stays accurate.
                raise InvalidStatusTransition(current_status, target_status)
            if (current_status, target_status) not in _VALID_TRANSITIONS:
                raise InvalidStatusTransition(current_status, target_status)

            now = datetime.now(UTC)
            await session.execute(
                text("UPDATE leas SET status = :s WHERE id = :id"),
                {"s": target_status, "id": lea_id},
            )

            await _write_audit_log(
                session=session,
                operator_id=operator_id,
                action="lea.status_changed",
                target_kind="lea",
                target_id=str(lea_id),
                lea_id=lea_id,
                reason=reason,
                detail={
                    "from_status": current_status,
                    "to_status": target_status,
                },
                created_at=now,
            )

            await session.commit()

        return LeaRow(
            id=lea_id,
            name=row.name,
            lea_type=row.lea_type,
            state=row.state,
            timezone=row.timezone,
            nces_lea_id=row.nces_lea_id,
            edlink_integration_id=row.edlink_integration_id,
            status=target_status,
        )

    async def get(self, lea_id: LeaId) -> LeaRow | None:
        """Return the LEA row for an id, or None if missing."""

        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, name, lea_type, state, timezone,"
                        " nces_lea_id, edlink_integration_id, status"
                        " FROM leas WHERE id = :id AND deleted_at IS NULL"
                    ),
                    {"id": lea_id},
                )
            ).first()
        if row is None:
            return None
        return LeaRow(
            id=LeaId(row.id),
            name=row.name,
            lea_type=row.lea_type,
            state=row.state,
            timezone=row.timezone,
            nces_lea_id=row.nces_lea_id,
            edlink_integration_id=row.edlink_integration_id,
            status=row.status,
        )

    async def _assert_unique(
        self, session: AsyncSession, params: CreateLeaInput
    ) -> None:
        existing = (
            await session.execute(
                text(
                    "SELECT id, name, edlink_integration_id FROM leas"
                    " WHERE id = :id OR name = :name"
                    " OR (edlink_integration_id IS NOT NULL"
                    "     AND edlink_integration_id = :integration)"
                ),
                {
                    "id": params.id,
                    "name": params.name,
                    "integration": params.edlink_integration_id,
                },
            )
        ).first()
        if existing is None:
            return
        if existing.id == params.id:
            raise LeaAlreadyExists(
                f"LEA with id {params.id!r} already exists."
            )
        if existing.name == params.name:
            raise LeaAlreadyExists(
                f"LEA with name {params.name!r} already exists."
            )
        raise LeaAlreadyExists(
            f"LEA with edlink_integration_id"
            f" {params.edlink_integration_id!r} already exists."
        )


async def _write_audit_log(
    *,
    session: AsyncSession,
    operator_id: uuid.UUID,
    action: str,
    target_kind: str,
    target_id: str,
    lea_id: LeaId | None,
    reason: str,
    detail: dict[str, Any],
    created_at: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO audit_log (
                operator_id, action, target_kind, target_id,
                lea_id, reason, detail, created_at
            ) VALUES (
                :op, :action, :kind, :tid,
                :lea, :reason, CAST(:detail AS JSONB), :now
            )
            """
        ),
        {
            "op": operator_id,
            "action": action,
            "kind": target_kind,
            "tid": target_id,
            "lea": lea_id,
            "reason": reason,
            "detail": json.dumps(detail),
            "now": created_at,
        },
    )


__all__ = [
    "CreateLeaInput",
    "InvalidStatusTransition",
    "LeaAdminError",
    "LeaAdminService",
    "LeaAlreadyExists",
    "LeaNotFound",
    "LeaRow",
    "LeaStatus",
]
