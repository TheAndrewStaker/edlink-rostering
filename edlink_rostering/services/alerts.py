"""Day-one alert service.

Wraps the four alerts the EdLink rostering design commits to shipping
on day one. Each alert is a small, testable function on
:class:`AlertService` that takes the minimum input it needs and emits a
structured telemetry event via the injected
:class:`~edlink_rostering.infrastructure.ports.TelemetryFacade`
facade. Production swaps the Telemetry implementation for the real
Azure Monitor exporter; the alert logic does not change.

The five alerts (one method each):

1. **Sync failure** (``alert.sync_failure``): a sync_job ended in
   ``status='failed'``. Emitted by the sync worker right after the
   failure transaction commits, keyed by ``sync_job_id`` so an alert
   cannot fire twice for the same sync.

2. **Schema drift** (``alert.schema_drift``): the validation report
   for a page recorded at least one Layer 2 error. Schema-shape errors
   are how partner-side schema drift surfaces; firing on Layer 2 catches
   "EdLink renamed a field" before it propagates.

3. **Quarantine growth** (``alert.quarantine_growth``): the
   ``quarantine`` table has more than ``quarantine_growth_threshold``
   unresolved rows for one LEA in a rolling 24h window. The default
   threshold (25 unresolved rows) is the conservative starting point
   from the design doc; operators tune per-LEA later.

4. **Cursor lag** (``alert.cursor_lag_20_day``): any LEA's
   ``cursor_state.last_event_at`` is more than 20 days behind ``now``.
   20 days is the design budget; EdLink's Events API retention is 30
   days, so a 20-day cursor lag means the LEA is within 10 days of a
   forced cold-start.

5. **Reconciliation drift** (``alert.reconciliation_drift``): the
   most recent ``reconciliation_runs`` row for a (lea, partner) within
   the rolling window completed with ``status='drift_detected'``. One
   alert per (lea, partner); only the latest run within the window
   reports so a wedged drift surfaces continuously without inflating
   the alert count on every daily run. Severity is ``warning`` because
   the alert is operator-actionable (investigate drift, re-pull
   canonical, force a bulk-load) rather than page-immediately.

Each alert carries a ``dedup_key`` property so downstream alerting
systems can suppress duplicates. The dedup key is derived from the
alert code plus the most specific identifier (``sync_job_id``,
``lea_id``, or ``lea_id+partner``).

The service does not page humans on its own. It writes structured
events to Telemetry; a separate Azure Monitor alert rule picks up the
events and pages on-call. Centralizing the rule logic here keeps the
alert taxonomy auditable in code review.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edlink_rostering.core.types import LeaId
from edlink_rostering.infrastructure.ports import TelemetryFacade
from edlink_rostering.services.validation import Severity

if TYPE_CHECKING:
    from edlink_rostering.services.validation import ValidationReport


CURSOR_LAG_THRESHOLD_DAYS = 20
"""Days behind ``now`` after which a cursor is alerted. The EdLink
Events API retention is 30 days; 20 days leaves 10-day headroom before
the cursor passes retention and forces cold-start bulk-load."""


QUARANTINE_GROWTH_THRESHOLD = 25
"""Unresolved quarantine rows per LEA per 24h that trigger the alert.
Conservative starting point; operators tune per-LEA later based on
observed patterns."""


IDEMPOTENCY_TABLE_SIZE_THRESHOLD = 100_000
"""Total ``idempotency_keys`` rows above which the alert fires. The
table is swept daily on a 24h budget by
:meth:`edlink_rostering.services.idempotency.IdempotencyService.sweep_stale`;
crossing this threshold means either the sweep stopped running or the
mutation volume grew enough to warrant tightening the retention
window. ADR-008's revisit trigger names this explicitly."""


@dataclass(frozen=True)
class AlertRecord:
    """Convenience structure summarizing what was alerted.

    Returned by every ``evaluate_*`` method so callers (tests, demo
    runner, ad-hoc CLI checks) can introspect without parsing the
    Telemetry stream.
    """

    code: str
    severity: str
    dedup_key: str
    properties: dict[str, str]
    measurements: dict[str, float]


class AlertService:
    """Encapsulates the four day-one alert evaluators."""

    def __init__(
        self,
        telemetry: TelemetryFacade,
        *,
        cursor_lag_threshold_days: int = CURSOR_LAG_THRESHOLD_DAYS,
        quarantine_growth_threshold: int = QUARANTINE_GROWTH_THRESHOLD,
        idempotency_table_size_threshold: int = (
            IDEMPOTENCY_TABLE_SIZE_THRESHOLD
        ),
    ) -> None:
        self._telemetry = telemetry
        self._cursor_lag_days = cursor_lag_threshold_days
        self._quarantine_threshold = quarantine_growth_threshold
        self._idempotency_threshold = idempotency_table_size_threshold

    def evaluate_sync_outcome(
        self,
        *,
        sync_job_id: uuid.UUID,
        lea_id: LeaId,
        partner: str,
        status: str,
        report: "ValidationReport",
        error_summary: str | None,
    ) -> list[AlertRecord]:
        """Evaluate sync_failure + schema_drift right after a page commits.

        Sync-failure fires when ``status='failed'``. Schema-drift fires
        when any Layer 2 error is in the report, regardless of overall
        page status (a partial-page schema break still warrants a
        signal; the page-blocking case is just the loudest version of
        the same issue).
        """

        records: list[AlertRecord] = []

        if status == "failed":
            records.append(
                self._emit(
                    code="alert.sync_failure",
                    severity="critical",
                    dedup_key=f"alert.sync_failure:{sync_job_id}",
                    properties={
                        "sync_job_id": str(sync_job_id),
                        "lea_id": str(lea_id),
                        "partner": partner,
                        "error_summary": error_summary or "",
                    },
                    measurements={
                        "error_count": float(report.error_count),
                    },
                )
            )

        layer_2_errors = [
            i
            for i in report.issues
            if i.layer == 2 and i.severity == Severity.ERROR
        ]
        if layer_2_errors:
            sample = layer_2_errors[0]
            records.append(
                self._emit(
                    code="alert.schema_drift",
                    severity="warning",
                    dedup_key=(
                        f"alert.schema_drift:{lea_id}:{partner}:{sample.code}"
                    ),
                    properties={
                        "sync_job_id": str(sync_job_id),
                        "lea_id": str(lea_id),
                        "partner": partner,
                        "sample_code": sample.code,
                        "sample_event_id": sample.event_id or "",
                    },
                    measurements={
                        "layer_2_error_count": float(len(layer_2_errors)),
                    },
                )
            )

        return records

    async def evaluate_quarantine_growth(
        self,
        session: AsyncSession,
        *,
        window_hours: int = 24,
    ) -> list[AlertRecord]:
        """Scan quarantine for LEAs exceeding ``quarantine_growth_threshold``.

        One emission per LEA over the threshold. Counts unresolved
        rows (``resolved_at IS NULL``) created in the trailing
        ``window_hours`` window so released or rejected rows do not
        count toward the alert.
        """

        rows = (
            await session.execute(
                text(
                    """
                    SELECT lea_id, COUNT(*) AS n
                    FROM quarantine
                    WHERE resolved_at IS NULL
                      AND created_at >= NOW() - (:hours * INTERVAL '1 hour')
                    GROUP BY lea_id
                    HAVING COUNT(*) > :threshold
                    """
                ),
                {
                    "hours": window_hours,
                    "threshold": self._quarantine_threshold,
                },
            )
        ).all()

        records: list[AlertRecord] = []
        for row in rows:
            records.append(
                self._emit(
                    code="alert.quarantine_growth",
                    severity="warning",
                    dedup_key=f"alert.quarantine_growth:{row.lea_id}",
                    properties={
                        "lea_id": str(row.lea_id),
                        "window_hours": str(window_hours),
                        "threshold": str(self._quarantine_threshold),
                    },
                    measurements={
                        "unresolved_count": float(row.n),
                    },
                )
            )
        return records

    async def evaluate_cursor_lag(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
    ) -> list[AlertRecord]:
        """Scan ``cursor_state`` for LEAs more than 20 days behind.

        Accepts an explicit ``now`` for testability so a synthetic
        ``last_event_at`` can be driven past the threshold without
        time-travel hacks at the call site.
        """

        moment = now or datetime.now(UTC)
        rows = (
            await session.execute(
                text(
                    """
                    SELECT lea_id, partner, last_event_id, last_event_at
                    FROM cursor_state
                    WHERE last_event_at IS NOT NULL
                      AND last_event_at < :cutoff
                    ORDER BY last_event_at ASC
                    """
                ),
                {
                    "cutoff": moment
                    - timedelta(days=self._cursor_lag_days),
                },
            )
        ).all()

        records: list[AlertRecord] = []
        for row in rows:
            days_behind = (moment - row.last_event_at).total_seconds() / 86400.0
            records.append(
                self._emit(
                    code="alert.cursor_lag_20_day",
                    severity="warning",
                    dedup_key=(
                        f"alert.cursor_lag_20_day:{row.lea_id}:{row.partner}"
                    ),
                    properties={
                        "lea_id": str(row.lea_id),
                        "partner": str(row.partner),
                        "last_event_id": row.last_event_id or "",
                        "last_event_at": row.last_event_at.isoformat(),
                        "threshold_days": str(self._cursor_lag_days),
                    },
                    measurements={
                        "days_behind": days_behind,
                    },
                )
            )
        return records

    async def evaluate_reconciliation_drift(
        self,
        session: AsyncSession,
        *,
        window_hours: int = 24,
    ) -> list[AlertRecord]:
        """Scan ``reconciliation_runs`` for the latest drift per (lea, partner).

        Picks the single most recent run per (lea, partner) within the
        rolling ``window_hours``. If that run is ``drift_detected``, one
        alert fires; if it is ``matched`` (a later daily run that
        recovered), no alert fires even though older drift rows are
        still in the window. This is what keeps the alert from
        flapping after an operator fixes the underlying drift.
        """

        rows = (
            await session.execute(
                text(
                    """
                    SELECT DISTINCT ON (lea_id, partner)
                        id,
                        lea_id,
                        partner,
                        status,
                        completed_at,
                        canonical_root_hash,
                        partner_root_hash,
                        drift_summary
                    FROM reconciliation_runs
                    WHERE completed_at >= NOW() - (:hours * INTERVAL '1 hour')
                    ORDER BY lea_id, partner, completed_at DESC
                    """
                ),
                {"hours": window_hours},
            )
        ).all()

        records: list[AlertRecord] = []
        for row in rows:
            if row.status != "drift_detected":
                continue
            drift_summary = row.drift_summary or []
            entity_types = [
                str(d.get("entity_type", "")) for d in drift_summary
            ]
            canonical_only_count = sum(
                len(d.get("canonical_only_ids", []) or [])
                for d in drift_summary
            )
            partner_only_count = sum(
                len(d.get("partner_only_ids", []) or [])
                for d in drift_summary
            )
            records.append(
                self._emit(
                    code="alert.reconciliation_drift",
                    severity="warning",
                    dedup_key=(
                        f"alert.reconciliation_drift:{row.lea_id}:{row.partner}"
                    ),
                    properties={
                        "run_id": str(row.id),
                        "lea_id": str(row.lea_id),
                        "partner": str(row.partner),
                        "completed_at": row.completed_at.isoformat(),
                        "entity_types": ",".join(entity_types),
                        "canonical_root_hash": row.canonical_root_hash or "",
                        "partner_root_hash": row.partner_root_hash or "",
                    },
                    measurements={
                        "entity_types_drifted": float(len(entity_types)),
                        "canonical_only_count": float(canonical_only_count),
                        "partner_only_count": float(partner_only_count),
                    },
                )
            )
        return records

    async def evaluate_idempotency_table_size(
        self, session: AsyncSession
    ) -> list[AlertRecord]:
        """Emit one alert when ``idempotency_keys`` exceeds the threshold.

        The 24h sweep job
        (:meth:`edlink_rostering.services.idempotency.IdempotencyService.sweep_stale`)
        bounds the table size in normal operation; crossing the
        threshold means either the sweep is wedged or the retention
        window needs tightening. ADR-008's "revisit if sweep-job cost
        becomes operationally meaningful" trigger is what this alert
        operationalizes.

        One record at most: the alert is per-table, not per-LEA.
        """

        row = (
            await session.execute(
                text("SELECT COUNT(*) AS n FROM idempotency_keys")
            )
        ).first()
        if row is None:
            return []
        count = int(row.n)
        if count <= self._idempotency_threshold:
            return []
        return [
            self._emit(
                code="alert.idempotency_table_growth",
                severity="warning",
                dedup_key="alert.idempotency_table_growth",
                properties={
                    "threshold": str(self._idempotency_threshold),
                },
                measurements={
                    "row_count": float(count),
                },
            )
        ]

    def fire_integration_degraded(
        self,
        *,
        lea_id: LeaId,
        partner: str,
        status: str,
    ) -> AlertRecord:
        """Fire ``alert.integration_degraded`` for a partner-side disable.

        EdLink (and any future partner) exposes a per-integration
        status enum the sync worker reads on every drain via the
        :class:`~edlink_rostering.services.integration_status.IntegrationStatusPoller`.
        ``inactive``, ``disabled``, and ``destroyed`` are degraded
        states; the poller calls this method so on-call sees the
        change without operators having to poll EdLink's portal.

        Severity is ``critical`` because a degraded integration
        means rostering is paused: subsequent polls would fail
        against a revoked or destroyed token. The dedup key is
        ``(alert code, lea, partner)`` so one alert per affected
        district per partner, no matter how many drain cycles pass
        before the operator clears it.
        """

        return self._emit(
            code="alert.integration_degraded",
            severity="critical",
            dedup_key=f"alert.integration_degraded:{lea_id}:{partner}",
            properties={
                "lea_id": str(lea_id),
                "partner": partner,
                "integration_status": status,
            },
            measurements={},
        )

    def _emit(
        self,
        *,
        code: str,
        severity: str,
        dedup_key: str,
        properties: dict[str, str],
        measurements: dict[str, float],
    ) -> AlertRecord:
        props = {
            **properties,
            "alert_code": code,
            "alert_severity": severity,
            "dedup_key": dedup_key,
        }
        self._telemetry.track_event(
            code, properties=props, measurements=measurements
        )
        return AlertRecord(
            code=code,
            severity=severity,
            dedup_key=dedup_key,
            properties=props,
            measurements=measurements,
        )


__all__ = [
    "CURSOR_LAG_THRESHOLD_DAYS",
    "IDEMPOTENCY_TABLE_SIZE_THRESHOLD",
    "QUARANTINE_GROWTH_THRESHOLD",
    "AlertRecord",
    "AlertService",
]
