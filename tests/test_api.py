"""Admin API smoke tests.

Uses FastAPI's ``TestClient`` (sync HTTP client) against the live
Postgres. Covers:

- Health endpoint returns ok.
- List LEAs returns the demo LEA after a sync run.
- Get sync detail returns validation + retry/revert history.
- Action endpoints reject requests without a JWT (401).
- Retry endpoint rewinds the cursor and writes the audit row.
- Quarantine endpoints list and act on rows.

The tests share one fixture that drives the existing sync worker
against the fixture LEA so the API tests run against real state.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.api import app
from edlink_rostering.connectors.edlink import EdLinkClient, EdLinkConnector
from edlink_rostering.core.types import LeaId
from edlink_rostering.infrastructure.azure_mocks import KeyVaultClient
from edlink_rostering.infrastructure.azure_mocks.app_insights import (
    MemorySink,
    Telemetry,
)
from edlink_rostering.services.sync_worker import SyncWorker
from tests.conftest import wipe_lea
from tests.fixtures.auth import auth_header, ensure_test_secret, mint_jwt


pytestmark = pytest.mark.skipif(
    not (os.environ.get("OPS_DATABASE_URL") or os.environ.get("DATABASE_URL")),
    reason="OPS_DATABASE_URL/DATABASE_URL not set; skipping HTTP tests",
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


_API_TEST_OPERATOR_SUBJECT = "test-api-operator-001"


@pytest_asyncio.fixture
async def qa_jwt(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Seed a test operator and yield a JWT for the action endpoints.

    Role is ``admin`` so the operator has implicit access to
    every LEA, mirroring the ``qa@edlink.test`` persona in the dev
    seed. Tests that need scoped 'operator' access live in
    ``test_multi_tenancy_enforcement.py`` and seed their own grants.
    """

    ensure_test_secret()
    op_id = uuid.uuid4()
    async with db_session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM operator_role WHERE operator_id IN "
                "(SELECT id FROM operator WHERE subject = :s)"
            ),
            {"s": _API_TEST_OPERATOR_SUBJECT},
        )
        await session.execute(
            text("DELETE FROM operator WHERE subject = :s"),
            {"s": _API_TEST_OPERATOR_SUBJECT},
        )
        await session.execute(
            text(
                """
                INSERT INTO operator
                    (id, subject, display_name, email, status)
                VALUES (:id, :sub, 'Test API Operator', :email, 'active')
                """
            ),
            {
                "id": op_id,
                "sub": _API_TEST_OPERATOR_SUBJECT,
                "email": "test-api-operator@edlink.test",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO operator_role
                    (id, operator_id, role, granted_by, reason)
                VALUES (:id, :op, 'admin', :op, 'test fixture')
                """
            ),
            {"id": uuid.uuid4(), "op": op_id},
        )
        await session.commit()

    token = mint_jwt(
        subject=_API_TEST_OPERATOR_SUBJECT,
        email="test-api-operator@edlink.test",
        name="Test API Operator",
    )
    yield auth_header(token)

    async with db_session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM operator_role WHERE operator_id IN "
                "(SELECT id FROM operator WHERE subject = :s)"
            ),
            {"s": _API_TEST_OPERATOR_SUBJECT},
        )
        await session.execute(
            text("DELETE FROM operator WHERE subject = :s"),
            {"s": _API_TEST_OPERATOR_SUBJECT},
        )
        await session.commit()


@pytest_asyncio.fixture
async def seeded_demo_lea(
    db_session_factory: async_sessionmaker[Any],
    edlink_fixtures_dir: Any,
) -> Any:
    """Wipe + re-seed lea-test-001 via a real sync worker pass."""

    lea_id = LeaId("lea-test-001")
    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()

    vault = KeyVaultClient()
    vault.put_secret(f"edlink-token-{lea_id}", "bearer-fake")
    connector = EdLinkConnector(
        client=EdLinkClient(fixtures_dir=edlink_fixtures_dir),
        key_vault=vault,
        session_factory=db_session_factory,
        page_size=6,
    )
    worker = SyncWorker(
        connector=connector,
        session_factory=db_session_factory,
        telemetry=Telemetry(sinks=[MemorySink()]),
    )
    outcomes = await worker.drain_lea(lea_id)
    yield lea_id, outcomes

    async with db_session_factory() as session:
        await wipe_lea(session, lea_id)
        await session.commit()


def test_health_returns_ok(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_list_leas_returns_seeded_lea(
    client: TestClient,
    seeded_demo_lea: tuple[LeaId, list],
    qa_jwt: dict[str, str],
) -> None:
    lea_id, _ = seeded_demo_lea
    r = client.get("/api/v1/leas", headers=qa_jwt)
    assert r.status_code == 200
    rows = r.json()
    ids = [row["id"] for row in rows]
    assert lea_id in ids
    row = next(row for row in rows if row["id"] == lea_id)
    assert row["student_count"] == 2  # carmen is soft-deleted
    assert row["enrollment_count"] == 3
    assert row["latest_sync_status"] == "success"


def test_get_sync_detail_returns_validation_issues(
    client: TestClient,
    seeded_demo_lea: tuple[LeaId, list],
    qa_jwt: dict[str, str],
) -> None:
    _, outcomes = seeded_demo_lea
    sync_job_id = str(outcomes[-1].sync_job_id)
    r = client.get(f"/api/v1/syncs/{sync_job_id}", headers=qa_jwt)
    assert r.status_code == 200
    body = r.json()
    assert body["sync"]["id"] == sync_job_id
    assert body["sync"]["status"] == "success"
    # Layer 5 always emits at least THRESHOLD_PAGE_OBSERVATION.
    codes = {iss["code"] for iss in body["validation_issues"]}
    assert "THRESHOLD_PAGE_OBSERVATION" in codes


def test_get_sync_detail_404_on_unknown(
    client: TestClient, qa_jwt: dict[str, str]
) -> None:
    r = client.get(f"/api/v1/syncs/{uuid.uuid4()}", headers=qa_jwt)
    assert r.status_code == 404


def test_retry_without_authorization_returns_401(
    client: TestClient, seeded_demo_lea: tuple[LeaId, list]
) -> None:
    _, outcomes = seeded_demo_lea
    r = client.post(
        f"/api/v1/syncs/{outcomes[-1].sync_job_id}/retry",
        json={"reason": "no header"},
    )
    assert r.status_code == 401


def test_retry_returns_409_on_success_without_force(
    client: TestClient,
    seeded_demo_lea: tuple[LeaId, list],
    qa_jwt: dict[str, str],
) -> None:
    _, outcomes = seeded_demo_lea
    r = client.post(
        f"/api/v1/syncs/{outcomes[-1].sync_job_id}/retry",
        headers=qa_jwt,
        json={"reason": "should refuse without force"},
    )
    assert r.status_code == 409


def test_retry_with_force_rewinds_cursor(
    client: TestClient,
    seeded_demo_lea: tuple[LeaId, list],
    qa_jwt: dict[str, str],
) -> None:
    lea_id, outcomes = seeded_demo_lea
    last = outcomes[-1]
    r = client.post(
        f"/api/v1/syncs/{last.sync_job_id}/retry",
        headers=qa_jwt,
        json={"reason": "test forced retry", "forced": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["forced"] is True
    assert body["cursor_rewound_to"] == last.cursor_before

    cursor = client.get(
        f"/api/v1/cursors?lea_id={lea_id}", headers=qa_jwt
    ).json()
    assert cursor[0]["last_event_id"] == last.cursor_before


def test_revert_action(
    client: TestClient,
    seeded_demo_lea: tuple[LeaId, list],
    qa_jwt: dict[str, str],
) -> None:
    _, outcomes = seeded_demo_lea
    last = outcomes[-1]
    r = client.post(
        f"/api/v1/syncs/{last.sync_job_id}/revert",
        headers=qa_jwt,
        json={"reason": "test revert"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["snapshots_restored"] >= 1


def test_alerts_endpoint_returns_array(
    client: TestClient, qa_jwt: dict[str, str]
) -> None:
    r = client.get("/api/v1/alerts", headers=qa_jwt)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_alerts_endpoint_without_auth_returns_401(
    client: TestClient,
) -> None:
    r = client.get("/api/v1/alerts")
    assert r.status_code == 401


def test_quarantine_list_endpoint_returns_array(
    client: TestClient, qa_jwt: dict[str, str]
) -> None:
    r = client.get("/api/v1/quarantine", headers=qa_jwt)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_quarantine_list_without_auth_returns_401(
    client: TestClient,
) -> None:
    r = client.get("/api/v1/quarantine")
    assert r.status_code == 401


def test_list_leas_without_auth_returns_401(
    client: TestClient,
) -> None:
    r = client.get("/api/v1/leas")
    assert r.status_code == 401


# ── Empty-reason rejection at the API boundary (Phase 1.5a Step 5) ──────────
#
# Every action endpoint that takes a 'reason' field declares it as
# Field(min_length=1) in edlink_rostering/api/schemas.py. The
# validation fires at request-parse time, before the handler runs, so
# an empty reason returns 422 regardless of whether the target id
# exists. The Phase 1.5a review (Push 4) flagged that this is what
# turns "reason is required" from a dialog nicety into a real
# guarantee; the tests below pin the contract.


def test_retry_with_empty_reason_returns_422(
    client: TestClient,
    seeded_demo_lea: tuple[LeaId, list],
    qa_jwt: dict[str, str],
) -> None:
    _, outcomes = seeded_demo_lea
    r = client.post(
        f"/api/v1/syncs/{outcomes[-1].sync_job_id}/retry",
        headers=qa_jwt,
        json={"reason": "", "forced": False},
    )
    assert r.status_code == 422
    assert "reason" in r.text.lower()


def test_revert_with_empty_reason_returns_422(
    client: TestClient,
    seeded_demo_lea: tuple[LeaId, list],
    qa_jwt: dict[str, str],
) -> None:
    _, outcomes = seeded_demo_lea
    r = client.post(
        f"/api/v1/syncs/{outcomes[-1].sync_job_id}/revert",
        headers=qa_jwt,
        json={"reason": ""},
    )
    assert r.status_code == 422
    assert "reason" in r.text.lower()


def test_quarantine_reject_with_empty_reason_returns_422(
    client: TestClient, qa_jwt: dict[str, str]
) -> None:
    # No fixture seeding required: schema validation fires before the
    # quarantine lookup runs, so a random uuid is fine.
    r = client.post(
        f"/api/v1/quarantine/{uuid.uuid4()}/reject",
        headers=qa_jwt,
        json={"reason": ""},
    )
    assert r.status_code == 422
    assert "reason" in r.text.lower()
