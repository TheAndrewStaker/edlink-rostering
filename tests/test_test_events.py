"""Tests for the dev-only "Send test event" dispatcher.

Covers:

1. Catalog. ``list_scenarios`` reads the fixture directory and returns
   nine scenarios across four sections.
2. Each scenario kind drives the right side effects on the
   pre-allocated sync_jobs row (happy delta, L1-L5, orphan
   quarantine, reconciliation drift).
3. The HTTP surface: catalog endpoint 404s without dev profile, auth
   gates work (admin required, operator-without-grant 403),
   dispatch endpoint 404s on an unknown scenario, dispatch endpoint
   returns the sync_job_id and the running row is committed before
   the background task runs.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.api import app
from edlink_rostering.core.types import LeaId
from edlink_rostering.services.test_events import (
    DEFAULT_VISIBILITY_SECONDS,
    ScenarioNotFound,
    TestEventService,
    load_scenarios,
)
from tests.conftest import wipe_lea
from tests.fixtures.auth import auth_header, ensure_test_secret, mint_jwt


pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("OPS_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    ),
    reason="OPS_DATABASE_URL/DATABASE_URL not set; skipping HTTP tests",
)


_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "edlink"
    / "test-events"
)


_TEST_EVENT_OPERATOR_SUBJECT = "test-event-operator-001"
_TEST_EVENT_OPERATOR_LIMITED = "test-event-operator-limited-001"


# ── Catalog ──────────────────────────────────────────────────────────


def test_load_scenarios_returns_nine_entries() -> None:
    scenarios = load_scenarios(_FIXTURES_DIR)
    assert len(scenarios) == 9
    sections = {s.section for s in scenarios.values()}
    assert sections == {"happy", "validation", "thresholds", "other"}
    ids = set(scenarios)
    assert {
        "happy_path",
        "l1_signature_mismatch",
        "l1_partner_unavailable",
        "l2_schema_missing_field",
        "l3_parse_invalid_date",
        "l4_orphan_enrollment",
        "l5_event_volume_spike",
        "l5_population_shift",
        "reconciliation_drift",
    }.issubset(ids)


def test_load_scenarios_empty_when_directory_missing(
    tmp_path: Path,
) -> None:
    assert load_scenarios(tmp_path / "missing") == {}


# ── Scenario handler shape (DB-bound) ────────────────────────────────


@pytest_asyncio.fixture
async def test_lea_id(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """A unique LEA per test so handler effects do not collide.

    The row is created up-front so the operator's `authorized_leas`
    set picks it up in the same request the dispatch endpoint
    authorizes against. Service-level tests do not strictly need the
    row pre-existing (the handler bootstraps it), but the HTTP path
    authorizes before the handler runs.
    """

    lea_id = LeaId(f"lea-test-event-{uuid.uuid4().hex[:8]}")
    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, :name, 'traditional_district', 'XX')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": lea_id, "name": f"Test Event LEA {lea_id[-8:]}"},
        )
        await session.commit()
    yield lea_id
    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()


def _service(
    factory: async_sessionmaker[Any],
) -> TestEventService:
    return TestEventService(
        session_factory=factory,
        fixtures_dir=_FIXTURES_DIR,
        running_visibility_seconds=0.0,
    )


@pytest.mark.asyncio
async def test_happy_path_writes_canonical_and_advances_cursor(
    db_session_factory: async_sessionmaker[Any],
    test_lea_id: LeaId,
) -> None:
    service = _service(db_session_factory)
    outcome = await service.run_immediately(
        lea_id=test_lea_id,
        scenario_id="happy_path",
        operator_subject="dev-tester",
    )

    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, event_count, cursor_after"
                    " FROM sync_jobs WHERE id = :id"
                ),
                {"id": outcome.sync_job_id},
            )
        ).one()
        assert row.status == "success"
        assert row.event_count == 2
        assert row.cursor_after is not None and row.cursor_after.startswith(
            "evt_test_"
        )

        student_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM students WHERE lea_id = :lea"
                ),
                {"lea": test_lea_id},
            )
        ).scalar_one()
        assert student_count == 1
        enrollment_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM enrollments WHERE lea_id = :lea"
                ),
                {"lea": test_lea_id},
            )
        ).scalar_one()
        assert enrollment_count == 1

        cursor = (
            await session.execute(
                text(
                    "SELECT last_event_id FROM cursor_state"
                    " WHERE lea_id = :lea AND partner = 'edlink'"
                ),
                {"lea": test_lea_id},
            )
        ).scalar_one()
        assert cursor == row.cursor_after


@pytest.mark.asyncio
async def test_l1_failure_marks_sync_failed_and_keeps_cursor(
    db_session_factory: async_sessionmaker[Any],
    test_lea_id: LeaId,
) -> None:
    service = _service(db_session_factory)
    outcome = await service.run_immediately(
        lea_id=test_lea_id,
        scenario_id="l1_signature_mismatch",
        operator_subject="dev-tester",
    )

    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, error_summary, cursor_after, cursor_before"
                    " FROM sync_jobs WHERE id = :id"
                ),
                {"id": outcome.sync_job_id},
            )
        ).one()
        assert row.status == "failed"
        assert row.error_summary == "L1:HTTP_INTEGRITY_FAILED"
        # Cursor must not advance on Layer 1 failure.
        assert row.cursor_after == row.cursor_before

        issues = (
            await session.execute(
                text(
                    "SELECT layer, code FROM sync_validation_results"
                    " WHERE sync_job_id = :id"
                ),
                {"id": outcome.sync_job_id},
            )
        ).all()
        assert any(i.layer == 1 for i in issues)


@pytest.mark.asyncio
async def test_l2_failure_emits_schema_error(
    db_session_factory: async_sessionmaker[Any],
    test_lea_id: LeaId,
) -> None:
    service = _service(db_session_factory)
    outcome = await service.run_immediately(
        lea_id=test_lea_id,
        scenario_id="l2_schema_missing_field",
        operator_subject="dev-tester",
    )
    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, error_summary"
                    " FROM sync_jobs WHERE id = :id"
                ),
                {"id": outcome.sync_job_id},
            )
        ).one()
        assert row.status == "failed"
        assert "L2:SCHEMA_MISSING_FIELD" in row.error_summary


@pytest.mark.asyncio
async def test_l3_failure_advances_cursor_with_per_event_error(
    db_session_factory: async_sessionmaker[Any],
    test_lea_id: LeaId,
) -> None:
    service = _service(db_session_factory)
    outcome = await service.run_immediately(
        lea_id=test_lea_id,
        scenario_id="l3_parse_invalid_date",
        operator_subject="dev-tester",
    )
    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, error_count, cursor_after"
                    " FROM sync_jobs WHERE id = :id"
                ),
                {"id": outcome.sync_job_id},
            )
        ).one()
        # L3 is per-event: the page still commits ("success") but the
        # validation result records the rejected event.
        assert row.status == "success"
        assert row.error_count == 1
        assert row.cursor_after is not None


@pytest.mark.asyncio
async def test_l4_orphan_opens_quarantine_row(
    db_session_factory: async_sessionmaker[Any],
    test_lea_id: LeaId,
) -> None:
    service = _service(db_session_factory)
    outcome = await service.run_immediately(
        lea_id=test_lea_id,
        scenario_id="l4_orphan_enrollment",
        operator_subject="dev-tester",
    )
    async with db_session_factory() as session:
        sync_status = (
            await session.execute(
                text("SELECT status FROM sync_jobs WHERE id = :id"),
                {"id": outcome.sync_job_id},
            )
        ).scalar_one()
        assert sync_status == "success"

        quarantine_rows = (
            await session.execute(
                text(
                    "SELECT entity_type, reason FROM quarantine"
                    " WHERE sync_job_id = :id"
                ),
                {"id": outcome.sync_job_id},
            )
        ).all()
        assert len(quarantine_rows) == 1
        assert quarantine_rows[0].entity_type == "enrollment"
        assert "Layer 4" in quarantine_rows[0].reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario_id,expected_code",
    [
        ("l5_event_volume_spike", "THRESHOLD_EVENT_VOLUME_SPIKE"),
        ("l5_population_shift", "THRESHOLD_POPULATION_SHIFT"),
    ],
)
async def test_l5_thresholds_page_block(
    db_session_factory: async_sessionmaker[Any],
    test_lea_id: LeaId,
    scenario_id: str,
    expected_code: str,
) -> None:
    service = _service(db_session_factory)
    outcome = await service.run_immediately(
        lea_id=test_lea_id,
        scenario_id=scenario_id,
        operator_subject="dev-tester",
    )
    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, error_summary"
                    " FROM sync_jobs WHERE id = :id"
                ),
                {"id": outcome.sync_job_id},
            )
        ).one()
        assert row.status == "failed"
        assert expected_code in row.error_summary


@pytest.mark.asyncio
async def test_reconciliation_drift_writes_runs_row(
    db_session_factory: async_sessionmaker[Any],
    test_lea_id: LeaId,
) -> None:
    service = _service(db_session_factory)
    outcome = await service.run_immediately(
        lea_id=test_lea_id,
        scenario_id="reconciliation_drift",
        operator_subject="dev-tester",
    )
    async with db_session_factory() as session:
        sync_status = (
            await session.execute(
                text("SELECT status FROM sync_jobs WHERE id = :id"),
                {"id": outcome.sync_job_id},
            )
        ).scalar_one()
        assert sync_status == "success"

        recon = (
            await session.execute(
                text(
                    "SELECT status, drift_summary"
                    " FROM reconciliation_runs WHERE lea_id = :lea"
                ),
                {"lea": test_lea_id},
            )
        ).one()
        assert recon.status == "drift_detected"
        assert recon.drift_summary[0]["entity_type"] == "students"


@pytest.mark.asyncio
async def test_unknown_scenario_id_raises(
    db_session_factory: async_sessionmaker[Any],
    test_lea_id: LeaId,
) -> None:
    service = _service(db_session_factory)
    with pytest.raises(ScenarioNotFound):
        await service.run_immediately(
            lea_id=test_lea_id,
            scenario_id="does_not_exist",
            operator_subject="dev-tester",
        )


# ── HTTP surface ─────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build the test client with dev profile on.

    The dev profile gates the router mount inside `create_app`. The
    module-level `app` was constructed before the test started, so a
    fresh build is needed for the dev surface to be wired up.
    """

    monkeypatch.setenv("EDLINK_PROFILE", "dev")
    from edlink_rostering.api.app import create_app

    return TestClient(create_app())


@pytest_asyncio.fixture
async def admin_jwt(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Seed a admin operator and yield an Authorization header."""

    ensure_test_secret()
    op_id = uuid.uuid4()
    async with db_session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM operator_role WHERE operator_id IN"
                " (SELECT id FROM operator WHERE subject = :s)"
            ),
            {"s": _TEST_EVENT_OPERATOR_SUBJECT},
        )
        await session.execute(
            text("DELETE FROM operator WHERE subject = :s"),
            {"s": _TEST_EVENT_OPERATOR_SUBJECT},
        )
        await session.execute(
            text(
                """
                INSERT INTO operator
                    (id, subject, display_name, email, status)
                VALUES
                    (:id, :sub, 'Test Event Operator', :email, 'active')
                """
            ),
            {
                "id": op_id,
                "sub": _TEST_EVENT_OPERATOR_SUBJECT,
                "email": "test-event-op@edlink.test",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO operator_role
                    (id, operator_id, role, granted_by, reason)
                VALUES
                    (:id, :op, 'admin', :op, 'test fixture')
                """
            ),
            {"id": uuid.uuid4(), "op": op_id},
        )
        await session.commit()

    token = mint_jwt(
        subject=_TEST_EVENT_OPERATOR_SUBJECT,
        email="test-event-op@edlink.test",
        name="Test Event Operator",
    )
    yield auth_header(token)

    async with db_session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM operator_role WHERE operator_id IN"
                " (SELECT id FROM operator WHERE subject = :s)"
            ),
            {"s": _TEST_EVENT_OPERATOR_SUBJECT},
        )
        await session.execute(
            text("DELETE FROM operator WHERE subject = :s"),
            {"s": _TEST_EVENT_OPERATOR_SUBJECT},
        )
        await session.commit()


@pytest_asyncio.fixture
async def auditor_jwt(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """An auditor operator. The dispatch endpoint must refuse this role."""

    ensure_test_secret()
    op_id = uuid.uuid4()
    async with db_session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM operator_role WHERE operator_id IN"
                " (SELECT id FROM operator WHERE subject = :s)"
            ),
            {"s": _TEST_EVENT_OPERATOR_LIMITED},
        )
        await session.execute(
            text("DELETE FROM operator WHERE subject = :s"),
            {"s": _TEST_EVENT_OPERATOR_LIMITED},
        )
        await session.execute(
            text(
                """
                INSERT INTO operator
                    (id, subject, display_name, email, status)
                VALUES
                    (:id, :sub, 'Limited Auditor', :email, 'active')
                """
            ),
            {
                "id": op_id,
                "sub": _TEST_EVENT_OPERATOR_LIMITED,
                "email": "limited-auditor@edlink.test",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO operator_role
                    (id, operator_id, role, granted_by, reason)
                VALUES
                    (:id, :op, 'auditor', :op, 'test fixture')
                """
            ),
            {"id": uuid.uuid4(), "op": op_id},
        )
        await session.commit()

    token = mint_jwt(
        subject=_TEST_EVENT_OPERATOR_LIMITED,
        email="limited-auditor@edlink.test",
        name="Limited Auditor",
    )
    yield auth_header(token)

    async with db_session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM operator_role WHERE operator_id IN"
                " (SELECT id FROM operator WHERE subject = :s)"
            ),
            {"s": _TEST_EVENT_OPERATOR_LIMITED},
        )
        await session.execute(
            text("DELETE FROM operator WHERE subject = :s"),
            {"s": _TEST_EVENT_OPERATOR_LIMITED},
        )
        await session.commit()


def test_catalog_endpoint_404_without_dev_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDLINK_PROFILE", raising=False)
    from edlink_rostering.api.app import create_app

    nondev = TestClient(create_app())
    r = nondev.get("/api/v1/dev/test-events/scenarios")
    assert r.status_code in (401, 404)


def test_catalog_endpoint_returns_nine_scenarios(
    client: TestClient,
    admin_jwt: dict[str, str],
) -> None:
    r = client.get("/api/v1/dev/test-events/scenarios", headers=admin_jwt)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["scenarios"]) == 9
    sections = {s["section"] for s in body["scenarios"]}
    assert sections == {"happy", "validation", "thresholds", "other"}


def test_dispatch_404_on_unknown_scenario(
    client: TestClient,
    admin_jwt: dict[str, str],
    test_lea_id: LeaId,
) -> None:
    r = client.post(
        "/api/v1/dev/test-events",
        json={"lea_id": test_lea_id, "scenario_id": "nope"},
        headers=admin_jwt,
    )
    assert r.status_code == 404


def test_dispatch_403_for_auditor_role(
    client: TestClient,
    auditor_jwt: dict[str, str],
) -> None:
    r = client.post(
        "/api/v1/dev/test-events",
        json={"lea_id": "lea-test-001", "scenario_id": "happy_path"},
        headers=auditor_jwt,
    )
    assert r.status_code == 403


def test_dispatch_returns_dispatch_response(
    client: TestClient,
    admin_jwt: dict[str, str],
    test_lea_id: LeaId,
) -> None:
    r = client.post(
        "/api/v1/dev/test-events",
        json={
            "lea_id": test_lea_id,
            "scenario_id": "happy_path",
        },
        headers=admin_jwt,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario_id"] == "happy_path"
    assert body["lea_id"] == test_lea_id
    assert body["running_visibility_seconds"] == pytest.approx(
        DEFAULT_VISIBILITY_SECONDS
    )
    # Background task validation lives in the service-level tests above;
    # the HTTP path only owns the contract that the running row is
    # committed before the response. The background sleep then keeps
    # the row in `running` long enough for React Query's poll cycle.
