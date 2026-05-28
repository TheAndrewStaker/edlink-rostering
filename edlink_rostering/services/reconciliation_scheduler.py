"""Daily reconciliation sweep across active connector authorizations.

Production deployment: an Azure Function timer trigger fires daily at
02:00 LEA-local time and calls :meth:`ReconciliationScheduler.run_daily_sweep`.
The sweep walks every ``connector_authorization`` row with
``status='active'`` and invokes
:meth:`edlink_rostering.services.reconciliation.ReconciliationService.reconcile_lea`
per (lea_id, partner). A per-LEA failure is logged and the sweep
continues; one bad LEA does not block reconciliation of the rest.

The same timer also calls
:meth:`edlink_rostering.services.idempotency.IdempotencyService.sweep_stale`
to bound ``idempotency_keys`` row count per ADR-008's 24h budget. The
two passes are independent: a failed reconciliation does not block the
idempotency sweep, and vice versa. Co-locating them keeps the
maintenance-timer surface to one entry point until Iain's day-one
walkthrough settles whether a separate maintenance worker should own
the idempotency sweep.

Two seams keep the scheduler connector-agnostic and test-friendly:

- ``snapshot_provider(partner, lea_id)`` returns the partner-side
  projection of canonical entities for the LEA. Production wires this
  to a dispatch table keyed by partner name (currently just
  ``edlink``: ``EdLinkConnector.walk_resources``). Tests pass a
  synthetic callable so the sweep can be exercised without HTTP.
- The scheduler reads active authorizations from a single SQL query
  so any operator-driven enable/disable in
  ``connector_authorization`` takes effect on the next sweep without
  redeployment.

Quiet-window semantics are inherited from
:class:`ReconciliationService`: the per-LEA reconcile short-circuits
to ``skipped_quiet_window`` if the cursor moved within the last 60
minutes. The sweep timer at 02:00 LEA-local is chosen because most
LEAs see no SIS activity overnight, so the quiet window almost always
holds. Forced reconciles bypass this via ``require_quiet_minutes=0``
in :meth:`reconcile_one`.
"""

from __future__ import annotations

import structlog
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId
from edlink_rostering.services.idempotency import IdempotencyService
from edlink_rostering.services.reconciliation import (
    ReconciliationReport,
    ReconciliationService,
)


logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


SnapshotProvider = Callable[
    [str, LeaId], Awaitable[dict[str, list[dict[str, Any]]]]
]


@dataclass(frozen=True)
class SweepReport:
    """Aggregate outcome of one daily sweep across active authorizations."""

    started_at: datetime
    completed_at: datetime
    total_authorizations: int
    matched_count: int
    drift_count: int
    skipped_count: int
    failed_count: int
    per_lea: list[ReconciliationReport] = field(default_factory=list)
    failures: list[tuple[LeaId, str, str]] = field(default_factory=list)
    idempotency_rows_swept: int = 0


class ReconciliationScheduler:
    """Walks active connector authorizations and reconciles each."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        reconciliation_service: ReconciliationService,
        snapshot_provider: SnapshotProvider,
        *,
        require_quiet_minutes: int = 60,
        idempotency_retention: timedelta = timedelta(hours=24),
    ) -> None:
        self._sessions = session_factory
        self._reconciliation = reconciliation_service
        self._snapshot_provider = snapshot_provider
        self._quiet_minutes = require_quiet_minutes
        self._idempotency_retention = idempotency_retention

    async def run_daily_sweep(self) -> SweepReport:
        """Reconcile every active (lea_id, partner) authorization.

        One per-LEA exception does not abort the sweep; failures are
        recorded in ``SweepReport.failures`` and the sweep continues.
        """

        started_at = datetime.now(UTC)
        targets = await self._active_authorizations()

        per_lea: list[ReconciliationReport] = []
        failures: list[tuple[LeaId, str, str]] = []
        matched = drift = skipped = failed = 0

        for lea_id, partner in targets:
            try:
                report = await self.reconcile_one(
                    lea_id=lea_id, partner=partner
                )
                per_lea.append(report)
                if report.status == "matched":
                    matched += 1
                elif report.status == "drift_detected":
                    drift += 1
                elif report.status == "skipped_quiet_window":
                    skipped += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                failures.append((lea_id, partner, str(exc)))
                logger.warning(
                    "reconciliation.sweep_partner_failed",
                    lea_id=lea_id,
                    partner=partner,
                    error=str(exc),
                )

        idempotency_rows_swept = await self._sweep_idempotency_keys()

        completed_at = datetime.now(UTC)
        return SweepReport(
            started_at=started_at,
            completed_at=completed_at,
            total_authorizations=len(targets),
            matched_count=matched,
            drift_count=drift,
            skipped_count=skipped,
            failed_count=failed,
            per_lea=per_lea,
            failures=failures,
            idempotency_rows_swept=idempotency_rows_swept,
        )

    async def _sweep_idempotency_keys(self) -> int:
        """Run the daily idempotency-keys sweep. Logs and swallows errors.

        The sweep is independent of reconciliation: a failure here
        must not abort the scheduler entry point or corrupt the
        SweepReport's reconciliation accounting. ADR-008 documents
        the retention budget; the implementation lives in
        :meth:`IdempotencyService.sweep_stale`.
        """

        try:
            service = IdempotencyService(self._sessions)
            deleted = await service.sweep_stale(
                older_than=self._idempotency_retention
            )
            logger.info(
                "idempotency.sweep_completed",
                rows_deleted=deleted,
                retention_hours=self._idempotency_retention.total_seconds()
                / 3600.0,
            )
            return deleted
        except Exception as exc:
            logger.warning("idempotency.sweep_failed", error=str(exc))
            return 0

    async def reconcile_one(
        self,
        *,
        lea_id: LeaId,
        partner: str,
        force: bool = False,
    ) -> ReconciliationReport:
        """Reconcile a single (lea_id, partner) pair.

        ``force=True`` bypasses the quiet-window check; the operator-
        driven forced-reconcile CLI surface uses this so an operator
        can investigate drift mid-day without waiting for the next
        02:00 sweep.
        """

        async def snapshot(target_lea: LeaId) -> dict[str, list[dict[str, Any]]]:
            return await self._snapshot_provider(partner, target_lea)

        quiet_minutes = 0 if force else self._quiet_minutes
        return await self._reconciliation.reconcile_lea(
            lea_id=lea_id,
            partner=partner,
            partner_snapshot=snapshot,
            require_quiet_minutes=quiet_minutes,
        )

    async def _active_authorizations(self) -> list[tuple[LeaId, str]]:
        """Return (lea_id, partner) pairs with an active authorization.

        Stable ordering by lea_id then partner so sweep output is
        deterministic across runs (helps log diffing and operator
        review).
        """

        async with self._sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT lea_id, partner
                        FROM connector_authorization
                        WHERE status = 'active'
                        ORDER BY lea_id, partner
                        """
                    )
                )
            ).all()
        return [(LeaId(row.lea_id), str(row.partner)) for row in rows]


__all__ = [
    "ReconciliationScheduler",
    "SnapshotProvider",
    "SweepReport",
]
