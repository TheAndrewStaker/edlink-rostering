"""AlertService tests.

Cover all four day-one alerts. Sync-failure and schema-drift are
exercised against synthetic ValidationReports (no DB). Quarantine-
growth and cursor-lag run against a real Postgres because the queries
are SQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.core.types import LeaId
from edlink_rostering.infrastructure.azure_mocks.app_insights import (
    MemorySink,
    Telemetry,
)
from edlink_rostering.services.alerts import AlertService
from edlink_rostering.services.validation import (
    Severity,
    ValidationIssue,
    ValidationReport,
)


@pytest.fixture
def telemetry() -> Telemetry:
    return Telemetry(sinks=[MemorySink()])


@pytest.fixture
def sink(telemetry: Telemetry) -> MemorySink:
    s = telemetry._sinks[0]
    assert isinstance(s, MemorySink)
    return s


@pytest.fixture
def service(telemetry: Telemetry) -> AlertService:
    return AlertService(telemetry=telemetry)


# ── Sync failure ─────────────────────────────────────────────────────────────


def test_sync_failure_fires_on_failed_status(
    service: AlertService, sink: MemorySink
) -> None:
    report = ValidationReport(
        issues=(
            ValidationIssue(
                layer=2,
                code="SCHEMA_MISSING_FIELD",
                severity=Severity.ERROR,
                detail={"field": "givenName"},
            ),
        ),
        ok_event_ids=(),
        quarantined_event_ids=(),
        page_blocked=True,
    )
    records = service.evaluate_sync_outcome(
        sync_job_id=uuid.uuid4(),
        lea_id=LeaId("lea-failtest"),
        partner="edlink",
        status="failed",
        report=report,
        error_summary="L2:SCHEMA_MISSING_FIELD",
    )
    codes = {r.code for r in records}
    assert "alert.sync_failure" in codes
    assert "alert.schema_drift" in codes
    emitted = {r.name for r in sink.records}
    assert "alert.sync_failure" in emitted
    assert "alert.schema_drift" in emitted


def test_sync_failure_does_not_fire_on_success(
    service: AlertService, sink: MemorySink
) -> None:
    report = ValidationReport(
        issues=(),
        ok_event_ids=("evt_1",),
        quarantined_event_ids=(),
        page_blocked=False,
    )
    records = service.evaluate_sync_outcome(
        sync_job_id=uuid.uuid4(),
        lea_id=LeaId("lea-happy"),
        partner="edlink",
        status="success",
        report=report,
        error_summary=None,
    )
    assert records == []
    assert sink.records == []


def test_schema_drift_fires_on_layer_2_error_even_when_partial(
    service: AlertService, sink: MemorySink
) -> None:
    report = ValidationReport(
        issues=(
            ValidationIssue(
                layer=2,
                code="SCHEMA_INVALID_GRADE",
                severity=Severity.ERROR,
                event_id="evt_42",
                detail={},
            ),
        ),
        ok_event_ids=("evt_1",),
        quarantined_event_ids=(),
        page_blocked=False,
    )
    records = service.evaluate_sync_outcome(
        sync_job_id=uuid.uuid4(),
        lea_id=LeaId("lea-drift"),
        partner="edlink",
        status="success",
        report=report,
        error_summary=None,
    )
    codes = [r.code for r in records]
    assert codes == ["alert.schema_drift"]


# ── Quarantine growth (DB-bound) ─────────────────────────────────────────────


@pytest_asyncio.fixture
async def isolated_lea(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Per-test LEA with cleanup that wipes every row touching it."""

    lea_id = LeaId(f"lea-alerts-{uuid.uuid4().hex[:8]}")
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, :n, 'traditional_district', 'XX')
                """
            ),
            {"id": lea_id, "n": f"alerts {lea_id}"},
        )
        await session.commit()

    yield lea_id

    from tests.conftest import wipe_lea

    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()


@pytest.mark.asyncio
async def test_quarantine_growth_fires_when_unresolved_exceeds_threshold(
    service: AlertService,
    db_session_factory: async_sessionmaker[Any],
    isolated_lea: LeaId,
) -> None:
    sync_job_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs (
                    id, lea_id, partner, status, started_at, completed_at,
                    event_count
                ) VALUES (
                    :id, :lea, 'edlink', 'success', :now, :now, 0
                )
                """
            ),
            {"id": sync_job_id, "lea": isolated_lea, "now": now},
        )
        for i in range(30):
            await session.execute(
                text(
                    """
                    INSERT INTO quarantine (
                        sync_job_id, lea_id, entity_type, entity_id,
                        reason, raw_payload, created_at
                    ) VALUES (
                        :sj, :lea, 'enrollment', :eid, 'Layer 4: orphan',
                        CAST('{}' AS JSONB), :now
                    )
                    """
                ),
                {
                    "sj": sync_job_id,
                    "lea": isolated_lea,
                    "eid": f"enr-{i}",
                    "now": now,
                },
            )
        await session.commit()

        records = await service.evaluate_quarantine_growth(session)

    matching = [r for r in records if r.properties["lea_id"] == isolated_lea]
    assert len(matching) == 1
    assert matching[0].measurements["unresolved_count"] == 30.0


@pytest.mark.asyncio
async def test_quarantine_growth_ignores_resolved_rows(
    service: AlertService,
    db_session_factory: async_sessionmaker[Any],
    isolated_lea: LeaId,
) -> None:
    """Resolved rows don't count toward the alert threshold."""

    sync_job_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs (
                    id, lea_id, partner, status, started_at, completed_at,
                    event_count
                ) VALUES (
                    :id, :lea, 'edlink', 'success', :now, :now, 0
                )
                """
            ),
            {"id": sync_job_id, "lea": isolated_lea, "now": now},
        )
        for i in range(30):
            await session.execute(
                text(
                    """
                    INSERT INTO quarantine (
                        sync_job_id, lea_id, entity_type, entity_id,
                        reason, raw_payload, created_at, resolved_at,
                        resolution_status
                    ) VALUES (
                        :sj, :lea, 'enrollment', :eid, 'Layer 4: orphan',
                        CAST('{}' AS JSONB), :now, :now, 'rejected'
                    )
                    """
                ),
                {
                    "sj": sync_job_id,
                    "lea": isolated_lea,
                    "eid": f"enr-{i}",
                    "now": now,
                },
            )
        await session.commit()

        records = await service.evaluate_quarantine_growth(session)

    assert not any(r.properties["lea_id"] == isolated_lea for r in records)


# ── Cursor lag (DB-bound) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cursor_lag_fires_for_20_day_old_cursor(
    service: AlertService,
    db_session_factory: async_sessionmaker[Any],
    isolated_lea: LeaId,
) -> None:
    now = datetime.now(UTC)
    stale = now - timedelta(days=22)
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO cursor_state (
                    lea_id, partner, last_event_id, last_event_at,
                    last_poll_at, cold_start_required, updated_at
                ) VALUES (
                    :lea, 'edlink', 'evt_stale', :stale, :now, false, :now
                )
                """
            ),
            {"lea": isolated_lea, "stale": stale, "now": now},
        )
        await session.commit()

        records = await service.evaluate_cursor_lag(session, now=now)

    matching = [r for r in records if r.properties["lea_id"] == isolated_lea]
    assert len(matching) == 1
    assert matching[0].measurements["days_behind"] > 20


@pytest.mark.asyncio
async def test_cursor_lag_does_not_fire_for_fresh_cursor(
    service: AlertService,
    db_session_factory: async_sessionmaker[Any],
    isolated_lea: LeaId,
) -> None:
    now = datetime.now(UTC)
    fresh = now - timedelta(days=1)
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO cursor_state (
                    lea_id, partner, last_event_id, last_event_at,
                    last_poll_at, cold_start_required, updated_at
                ) VALUES (
                    :lea, 'edlink', 'evt_fresh', :fresh, :now, false, :now
                )
                """
            ),
            {"lea": isolated_lea, "fresh": fresh, "now": now},
        )
        await session.commit()

        records = await service.evaluate_cursor_lag(session, now=now)

    assert not any(r.properties["lea_id"] == isolated_lea for r in records)


# ── Reconciliation drift (DB-bound) ──────────────────────────────────────────


async def _insert_recon_run(
    session: Any,
    *,
    lea_id: LeaId,
    partner: str,
    status: str,
    completed_at: datetime,
    drift_summary: list[dict[str, Any]] | None,
    canonical_root_hash: str = "abc",
    partner_root_hash: str | None = "def",
) -> uuid.UUID:
    """Insert a reconciliation_runs row directly for alert-side testing."""

    import json

    run_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO reconciliation_runs (
                id, lea_id, partner, started_at, completed_at,
                status, canonical_root_hash, partner_root_hash,
                drift_summary, error_message
            ) VALUES (
                :id, :lea, :partner, :started, :completed,
                :status, :canonical, :partner_hash,
                CAST(:drift AS JSONB), NULL
            )
            """
        ),
        {
            "id": run_id,
            "lea": lea_id,
            "partner": partner,
            "started": completed_at - timedelta(seconds=5),
            "completed": completed_at,
            "status": status,
            "canonical": canonical_root_hash,
            "partner_hash": partner_root_hash,
            "drift": (
                json.dumps(drift_summary) if drift_summary is not None else None
            ),
        },
    )
    return run_id


@pytest.mark.asyncio
async def test_reconciliation_drift_fires_on_recent_drift_run(
    service: AlertService,
    db_session_factory: async_sessionmaker[Any],
    isolated_lea: LeaId,
) -> None:
    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await _insert_recon_run(
            session,
            lea_id=isolated_lea,
            partner="edlink",
            status="drift_detected",
            completed_at=now - timedelta(hours=2),
            drift_summary=[
                {
                    "entity_type": "students",
                    "canonical_only_ids": ["stu-only-1", "stu-only-2"],
                    "partner_only_ids": ["stu-extra"],
                    "canonical_mid_hash": "c-hash",
                    "partner_mid_hash": "p-hash",
                }
            ],
        )
        await session.commit()

        records = await service.evaluate_reconciliation_drift(session)

    matching = [r for r in records if r.properties["lea_id"] == isolated_lea]
    assert len(matching) == 1
    rec = matching[0]
    assert rec.code == "alert.reconciliation_drift"
    assert rec.severity == "warning"
    assert rec.properties["partner"] == "edlink"
    assert rec.properties["entity_types"] == "students"
    assert rec.measurements["entity_types_drifted"] == 1.0
    assert rec.measurements["canonical_only_count"] == 2.0
    assert rec.measurements["partner_only_count"] == 1.0


@pytest.mark.asyncio
async def test_reconciliation_drift_does_not_fire_when_latest_matched(
    service: AlertService,
    db_session_factory: async_sessionmaker[Any],
    isolated_lea: LeaId,
) -> None:
    """Older drift + newer matched within the window = no alert.

    The latest run per (lea, partner) is what drives the alert; an
    operator who fixed the drift should see the alert disappear on the
    next daily reconcile.
    """

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await _insert_recon_run(
            session,
            lea_id=isolated_lea,
            partner="edlink",
            status="drift_detected",
            completed_at=now - timedelta(hours=18),
            drift_summary=[
                {
                    "entity_type": "enrollments",
                    "canonical_only_ids": ["enr-old"],
                    "partner_only_ids": [],
                    "canonical_mid_hash": "c1",
                    "partner_mid_hash": "p1",
                }
            ],
        )
        await _insert_recon_run(
            session,
            lea_id=isolated_lea,
            partner="edlink",
            status="matched",
            completed_at=now - timedelta(hours=1),
            drift_summary=None,
        )
        await session.commit()

        records = await service.evaluate_reconciliation_drift(session)

    assert not any(r.properties["lea_id"] == isolated_lea for r in records)


@pytest.mark.asyncio
async def test_reconciliation_drift_ignores_runs_outside_window(
    service: AlertService,
    db_session_factory: async_sessionmaker[Any],
    isolated_lea: LeaId,
) -> None:
    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await _insert_recon_run(
            session,
            lea_id=isolated_lea,
            partner="edlink",
            status="drift_detected",
            completed_at=now - timedelta(hours=48),
            drift_summary=[
                {
                    "entity_type": "students",
                    "canonical_only_ids": [],
                    "partner_only_ids": ["stu-extra"],
                    "canonical_mid_hash": "c",
                    "partner_mid_hash": "p",
                }
            ],
        )
        await session.commit()

        records = await service.evaluate_reconciliation_drift(
            session, window_hours=24
        )

    assert not any(r.properties["lea_id"] == isolated_lea for r in records)


@pytest.mark.asyncio
async def test_reconciliation_drift_dedups_by_lea_partner(
    service: AlertService,
    db_session_factory: async_sessionmaker[Any],
    isolated_lea: LeaId,
) -> None:
    """Two drift runs in the window for one (lea, partner) → one alert.

    The DISTINCT ON keeps the latest; older drift in the same pair does
    not double-fire.
    """

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await _insert_recon_run(
            session,
            lea_id=isolated_lea,
            partner="edlink",
            status="drift_detected",
            completed_at=now - timedelta(hours=20),
            drift_summary=[
                {
                    "entity_type": "students",
                    "canonical_only_ids": [],
                    "partner_only_ids": ["earlier"],
                    "canonical_mid_hash": "c-old",
                    "partner_mid_hash": "p-old",
                }
            ],
        )
        latest_id = await _insert_recon_run(
            session,
            lea_id=isolated_lea,
            partner="edlink",
            status="drift_detected",
            completed_at=now - timedelta(hours=1),
            drift_summary=[
                {
                    "entity_type": "students",
                    "canonical_only_ids": [],
                    "partner_only_ids": ["later"],
                    "canonical_mid_hash": "c-new",
                    "partner_mid_hash": "p-new",
                }
            ],
        )
        await session.commit()

        records = await service.evaluate_reconciliation_drift(session)

    matching = [r for r in records if r.properties["lea_id"] == isolated_lea]
    assert len(matching) == 1
    assert matching[0].properties["run_id"] == str(latest_id)
