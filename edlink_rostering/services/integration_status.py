"""Integration-status polling and persistence.

EdLink exposes a per-integration ``status`` enum (``inactive``,
``active``, ``requested``, ``disabled``, ``destroyed``) and a
``sharing_scope`` value that captures what the district has granted
the integration access to. Both can change after a connector is
authorized: a district can disable the integration in EdLink's
portal, EdLink can mark it ``destroyed`` after a deauth, or the
sharing scope can narrow during a re-auth flow.

The sync worker polls EdLink for this state on every drain and
persists it on ``connector_authorization`` so the admin app surfaces
degraded integrations without re-fetching from EdLink. Degraded
states (``inactive``, ``disabled``, ``destroyed``) drive an
``integration_degraded`` alert so on-call sees the partner-side
change quickly.

Why a separate service:

- The poll + persist is connector-agnostic in shape (one snapshot
  per partner+LEA, status enum) but partner-specific in transport
  (EdLink's per-integration endpoint vs whatever the next partner
  exposes). Keeping it out of ``SyncWorker`` lets a future partner
  wire a different transport without touching the page-processing
  loop.

- The poll is cheap and idempotent. It runs before the drain so a
  ``disabled`` integration's drain is skipped, avoiding a page of
  failed requests against a paused token.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId
from edlink_rostering.services.alerts import AlertService


logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


DEGRADED_STATUSES: frozenset[str] = frozenset(
    {"inactive", "disabled", "destroyed"}
)


@dataclass(frozen=True)
class IntegrationStatusRecord:
    """One persisted integration-status row from connector_authorization."""

    lea_id: LeaId
    partner: str
    status: str
    sharing_scope: str | None
    observed_at: datetime | None
    is_degraded: bool


class IntegrationStatusProbe(Protocol):
    """Structural shape the poller needs from a connector.

    EdLinkConnector implements this via ``get_integration_status``.
    A future partner connector implements the same method against
    its own endpoint; the poller does not import partner modules.
    """

    name: str

    async def get_integration_status(
        self, lea_id: LeaId
    ) -> "IntegrationStatusSnapshot": ...


@dataclass(frozen=True)
class IntegrationStatusSnapshot:
    """Same shape as the connector module's IntegrationStatusSnapshot.

    Re-declared here so this module does not import the connectors
    package directly (one-way dependency: connectors -> services is
    fine, but the reverse closes a cycle through the CLI wiring).
    """

    status: str
    sharing_scope: str
    observed_at: datetime

    @property
    def is_degraded(self) -> bool:
        return self.status in DEGRADED_STATUSES


class IntegrationStatusPoller:
    """Polls a connector and persists the snapshot on connector_authorization."""

    def __init__(
        self,
        connector: Any,
        session_factory: async_sessionmaker[AsyncSession],
        alerts: AlertService | None = None,
    ) -> None:
        self._connector = connector
        self._sessions = session_factory
        self._alerts = alerts

    async def poll_and_persist(
        self, lea_id: LeaId
    ) -> IntegrationStatusRecord | None:
        """Poll the partner, persist the result, fire the alert if degraded.

        Returns the persisted record so callers (the sync worker)
        can decide whether to skip the drain. Returns None when no
        live ``connector_authorization`` row exists for the
        (lea, partner) pair, since there is nowhere to persist the
        result.
        """

        if not hasattr(self._connector, "get_integration_status"):
            return None

        snapshot = await self._connector.get_integration_status(lea_id)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        """
                        UPDATE connector_authorization
                        SET integration_status = :status,
                            sharing_scope = :scope,
                            integration_status_observed_at = :observed
                        WHERE lea_id = :lea
                          AND partner = :partner
                          AND revoked_at IS NULL
                        RETURNING integration_status,
                                  sharing_scope,
                                  integration_status_observed_at
                        """
                    ),
                    {
                        "status": snapshot.status,
                        "scope": snapshot.sharing_scope,
                        "observed": snapshot.observed_at,
                        "lea": lea_id,
                        "partner": self._connector.name,
                    },
                )
            ).first()
            await session.commit()

        if row is None:
            return None

        record = IntegrationStatusRecord(
            lea_id=lea_id,
            partner=self._connector.name,
            status=row.integration_status,
            sharing_scope=row.sharing_scope,
            observed_at=row.integration_status_observed_at,
            is_degraded=row.integration_status in DEGRADED_STATUSES,
        )

        if record.is_degraded:
            logger.warning(
                "integration.degraded",
                lea_id=lea_id,
                partner=self._connector.name,
                status=record.status,
            )
            if self._alerts is not None:
                self._alerts.fire_integration_degraded(
                    lea_id=lea_id,
                    partner=self._connector.name,
                    status=record.status,
                )

        return record


__all__ = [
    "DEGRADED_STATUSES",
    "IntegrationStatusPoller",
    "IntegrationStatusProbe",
    "IntegrationStatusRecord",
    "IntegrationStatusSnapshot",
]
