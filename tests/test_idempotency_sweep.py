"""Idempotency sweep + table-size alert tests.

Closes the unbounded-table risk ADR-008 named in the "revisit if
sweep-job cost becomes operationally meaningful" trigger. Three
layers under test:

1. :meth:`IdempotencyService.sweep_stale` deletes rows older than the
   retention budget (default 24h) and leaves fresh rows untouched.
2. :meth:`AlertService.evaluate_idempotency_table_size` emits one
   ``alert.idempotency_table_growth`` record when the row count
   exceeds the configured threshold and stays quiet otherwise.
3. :meth:`ReconciliationScheduler.run_daily_sweep` invokes the
   idempotency sweep after the reconciliation pass and records the
   row count it deleted on the SweepReport.
"""

from __future__ import annotations

import json
import os
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
from edlink_rostering.services.idempotency import IdempotencyService
from edlink_rostering.services.reconciliation import ReconciliationService
from edlink_rostering.services.reconciliation_scheduler import (
    ReconciliationScheduler,
)


pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("OPS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    ),
    reason="OPS_DATABASE_URL/DATABASE_URL not set; skipping DB-bound tests",
)


@pytest_asyncio.fixture
async def isolated_operator(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """A throwaway operator row used as the FK target for idempotency rows."""

    op_id = uuid.uuid4()
    subject = f"idem-sweep-{uuid.uuid4().hex[:8]}"
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO operator (
                    id, subject, display_name, email, status
                ) VALUES (:id, :sub, 'Sweep Test', :email, 'active')
                """
            ),
            {"id": op_id, "sub": subject, "email": f"{subject}@edlink.test"},
        )
        await session.commit()

    yield op_id

    async with db_session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM idempotency_keys WHERE operator_id = :op"
            ),
            {"op": op_id},
        )
        await session.execute(
            text("DELETE FROM operator WHERE id = :op"),
            {"op": op_id},
        )
        await session.commit()


async def _insert_idempotency_row(
    factory: async_sessionmaker[Any],
    *,
    operator_id: uuid.UUID,
    key: str,
    created_at: datetime,
    completed: bool = True,
) -> None:
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO idempotency_keys (
                    operator_id, route, key, request_hash,
                    response_status, response_body, created_at, completed_at
                ) VALUES (
                    :op, 'test.route', :key, 'h',
                    :status, CAST(:body AS JSONB), :created, :completed
                )
                """
            ),
            {
                "op": operator_id,
                "key": key,
                "status": 200 if completed else None,
                "body": json.dumps({"ok": True}) if completed else None,
                "created": created_at,
                "completed": created_at if completed else None,
            },
        )
        await session.commit()


# ── Sweep ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_deletes_rows_older_than_retention(
    db_session_factory: async_sessionmaker[Any],
    isolated_operator: uuid.UUID,
) -> None:
    """A 25-hour-old row goes; a 1-hour-old row stays."""

    now = datetime.now(UTC)
    await _insert_idempotency_row(
        db_session_factory,
        operator_id=isolated_operator,
        key="stale-key",
        created_at=now - timedelta(hours=25),
    )
    await _insert_idempotency_row(
        db_session_factory,
        operator_id=isolated_operator,
        key="fresh-key",
        created_at=now - timedelta(hours=1),
    )

    service = IdempotencyService(db_session_factory)
    deleted = await service.sweep_stale(older_than=timedelta(hours=24))

    assert deleted == 1
    async with db_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT key FROM idempotency_keys"
                    " WHERE operator_id = :op"
                ),
                {"op": isolated_operator},
            )
        ).all()
    keys = {r.key for r in rows}
    assert keys == {"fresh-key"}


@pytest.mark.asyncio
async def test_sweep_reclaims_stuck_pending_rows(
    db_session_factory: async_sessionmaker[Any],
    isolated_operator: uuid.UUID,
) -> None:
    """A pending row that aged past retention is also reclaimed.

    The wrapper's ``discard`` only removes pending rows on handler
    failure; a process crash mid-handler can leave a pending row that
    no caller will ever clean. The sweep is the safety net.
    """

    await _insert_idempotency_row(
        db_session_factory,
        operator_id=isolated_operator,
        key="stuck-pending",
        created_at=datetime.now(UTC) - timedelta(hours=25),
        completed=False,
    )

    service = IdempotencyService(db_session_factory)
    deleted = await service.sweep_stale(older_than=timedelta(hours=24))

    assert deleted == 1


@pytest.mark.asyncio
async def test_sweep_is_a_noop_when_no_rows_qualify(
    db_session_factory: async_sessionmaker[Any],
    isolated_operator: uuid.UUID,
) -> None:
    await _insert_idempotency_row(
        db_session_factory,
        operator_id=isolated_operator,
        key="fresh-key",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )

    service = IdempotencyService(db_session_factory)
    deleted = await service.sweep_stale(older_than=timedelta(hours=24))

    assert deleted == 0


# ── Alert ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_table_size_alert_fires_above_threshold(
    db_session_factory: async_sessionmaker[Any],
    isolated_operator: uuid.UUID,
) -> None:
    """Synthetic 5-row threshold keeps the test cheap and isolated."""

    now = datetime.now(UTC)
    for i in range(6):
        await _insert_idempotency_row(
            db_session_factory,
            operator_id=isolated_operator,
            key=f"alert-key-{i}",
            created_at=now - timedelta(minutes=i),
        )

    telemetry = Telemetry(sinks=[MemorySink()])
    service = AlertService(
        telemetry=telemetry, idempotency_table_size_threshold=5
    )
    async with db_session_factory() as session:
        records = await service.evaluate_idempotency_table_size(session)

    assert len(records) == 1
    record = records[0]
    assert record.code == "alert.idempotency_table_growth"
    assert record.severity == "warning"
    assert record.measurements["row_count"] >= 6
    assert record.properties["threshold"] == "5"


@pytest.mark.asyncio
async def test_table_size_alert_quiet_at_or_below_threshold(
    db_session_factory: async_sessionmaker[Any],
    isolated_operator: uuid.UUID,
) -> None:
    """No alert when the row count is at or below the threshold."""

    # Wipe any rows accumulated by parallel tests so this assertion
    # is deterministic in shared-DB runs.
    async with db_session_factory() as session:
        await session.execute(text("DELETE FROM idempotency_keys"))
        await session.commit()

    await _insert_idempotency_row(
        db_session_factory,
        operator_id=isolated_operator,
        key="quiet-key",
        created_at=datetime.now(UTC),
    )

    telemetry = Telemetry(sinks=[MemorySink()])
    service = AlertService(
        telemetry=telemetry, idempotency_table_size_threshold=5
    )
    async with db_session_factory() as session:
        records = await service.evaluate_idempotency_table_size(session)

    assert records == []


# ── Scheduler integration ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_daily_sweep_invokes_idempotency_sweep(
    db_session_factory: async_sessionmaker[Any],
    isolated_operator: uuid.UUID,
) -> None:
    """The maintenance pass runs even when there are no authorizations."""

    now = datetime.now(UTC)
    await _insert_idempotency_row(
        db_session_factory,
        operator_id=isolated_operator,
        key="sched-stale",
        created_at=now - timedelta(hours=25),
    )
    await _insert_idempotency_row(
        db_session_factory,
        operator_id=isolated_operator,
        key="sched-fresh",
        created_at=now - timedelta(hours=1),
    )

    async def empty_snapshot(
        partner: str, lea_id: LeaId
    ) -> dict[str, list[dict[str, Any]]]:
        return {"students": [], "enrollments": []}

    recon_service = ReconciliationService(session_factory=db_session_factory)
    scheduler = ReconciliationScheduler(
        session_factory=db_session_factory,
        reconciliation_service=recon_service,
        snapshot_provider=empty_snapshot,
    )

    report = await scheduler.run_daily_sweep()

    assert report.idempotency_rows_swept >= 1
    async with db_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT key FROM idempotency_keys"
                    " WHERE operator_id = :op"
                ),
                {"op": isolated_operator},
            )
        ).all()
    keys = {r.key for r in rows}
    assert "sched-stale" not in keys
    assert "sched-fresh" in keys
