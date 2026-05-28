"""Multi-tenancy enforcement on state-mutating action endpoints.

Step 3 of Phase 1.5a's strong-hire gate. Each action endpoint loads
the target's ``lea_id`` via the ``_lookups`` helper, then compares it
to the operator's ``authorized_leas`` before the action service runs.
The asymmetry the tests pin:

* an operator scoped to LEA A cannot touch LEA B (403)
* an unknown id is 404, never 403, so the operator cannot infer that
  the id exists in another LEA
* owner and admin satisfy every scope check
* the authz check happens BEFORE the service, so the response is 403
  even when the service would have refused with 409 for unrelated
  reasons

The tests seed two LEAs with one sync_job and one quarantine row
each, plus three operator personas (single-LEA, admin,
owner), and exercise every action endpoint against every
scope combination the plan called out.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.api import app
from tests.conftest import wipe_lea
from tests.fixtures.auth import auth_header, ensure_test_secret, mint_jwt

pytestmark = pytest.mark.skipif(
    not (os.environ.get("OPS_DATABASE_URL") or os.environ.get("DATABASE_URL")),
    reason="OPS_DATABASE_URL/DATABASE_URL not set; skipping enforcement tests",
)


LEA_A = "lea-tenancy-a"
LEA_B = "lea-tenancy-b"

_SUBJECT_OPERATOR_A = "test-tenancy-operator-a-only-001"
_SUBJECT_CONNECTOR_ADMIN = "test-tenancy-connector-admin-001"
_SUBJECT_FOUNDER_ADMIN = "test-tenancy-founder-admin-001"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest_asyncio.fixture
async def tenancy_world(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Seed two LEAs, two sync_jobs, two quarantine rows, three operators.

    Yields a dict with all the ids the tests need. Cleans up every
    row it inserted regardless of test outcome.
    """

    ensure_test_secret()

    sync_a = uuid.uuid4()
    sync_b = uuid.uuid4()
    quar_a = uuid.uuid4()
    quar_b = uuid.uuid4()

    op_a_id = uuid.uuid4()
    op_connector_id = uuid.uuid4()
    op_founder_id = uuid.uuid4()

    now = datetime.now(UTC)

    async with db_session_factory() as session:
        # LEAs (idempotent guard against leftover rows from a prior
        # crashed run).
        await wipe_lea(session, LEA_A)
        await wipe_lea(session, LEA_B)
        await _wipe_tenancy_operators(session)

        for lea_id in (LEA_A, LEA_B):
            await session.execute(
                text(
                    """
                    INSERT INTO leas
                        (id, name, lea_type, state)
                    VALUES (:id, :name, 'traditional_district', 'CA')
                    """
                ),
                {"id": lea_id, "name": f"Tenancy Test {lea_id}"},
            )

        # One sync_job + one quarantine row per LEA. status='failed'
        # so a retry without --force lands on the 409 refusal path,
        # which still proves the authz layer passed when expected.
        for sync_job_id, quarantine_id, lea_id in (
            (sync_a, quar_a, LEA_A),
            (sync_b, quar_b, LEA_B),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO sync_jobs
                        (id, lea_id, partner, status, started_at,
                         event_count, error_count, warning_count,
                         cursor_before, cursor_after)
                    VALUES (:id, :lea, 'edlink', 'failed', :now,
                            0, 1, 0, 'evt_pre', NULL)
                    """
                ),
                {"id": sync_job_id, "lea": lea_id, "now": now},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO quarantine
                        (id, sync_job_id, lea_id, entity_type,
                         entity_id, reason, raw_payload, created_at)
                    VALUES (:id, :sj, :lea, 'enrollment',
                            :entity_id, 'orphan',
                            CAST('{}' AS JSONB), :now)
                    """
                ),
                {
                    "id": quarantine_id,
                    "sj": sync_job_id,
                    "lea": lea_id,
                    "entity_id": f"enr-orphan-{lea_id}",
                    "now": now,
                },
            )

        # Operators with three different scope shapes.
        for op_id, subject, name, email, role in (
            (
                op_founder_id,
                _SUBJECT_FOUNDER_ADMIN,
                "Tenancy Founder",
                "tenancy-founder@edlink.test",
                "owner",
            ),
            (
                op_connector_id,
                _SUBJECT_CONNECTOR_ADMIN,
                "Tenancy Connector Admin",
                "tenancy-connector@edlink.test",
                "admin",
            ),
            (
                op_a_id,
                _SUBJECT_OPERATOR_A,
                "Tenancy LEA-A Operator",
                "tenancy-operator-a@edlink.test",
                "operator",
            ),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO operator
                        (id, subject, display_name, email, status)
                    VALUES (:id, :sub, :name, :email, 'active')
                    """
                ),
                {
                    "id": op_id,
                    "sub": subject,
                    "name": name,
                    "email": email,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO operator_role
                        (id, operator_id, role, granted_by, reason)
                    VALUES (:id, :op, :role, :by, 'test fixture')
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "op": op_id,
                    "role": role,
                    "by": op_founder_id,
                },
            )

        # Single-LEA scope for the operator persona.
        await session.execute(
            text(
                """
                INSERT INTO operator_lea_grant
                    (id, operator_id, lea_id, granted_by, reason)
                VALUES (:id, :op, :lea, :by, 'test fixture')
                """
            ),
            {
                "id": uuid.uuid4(),
                "op": op_a_id,
                "lea": LEA_A,
                "by": op_founder_id,
            },
        )

        await session.commit()

    yield {
        "sync_a": sync_a,
        "sync_b": sync_b,
        "quar_a": quar_a,
        "quar_b": quar_b,
        "operator_a_jwt": mint_jwt(
            subject=_SUBJECT_OPERATOR_A,
            email="tenancy-operator-a@edlink.test",
            name="Tenancy LEA-A Operator",
        ),
        "admin_jwt": mint_jwt(
            subject=_SUBJECT_CONNECTOR_ADMIN,
            email="tenancy-connector@edlink.test",
            name="Tenancy Connector Admin",
        ),
        "owner_jwt": mint_jwt(
            subject=_SUBJECT_FOUNDER_ADMIN,
            email="tenancy-founder@edlink.test",
            name="Tenancy Founder",
        ),
    }

    async with db_session_factory() as session:
        await _wipe_tenancy_operators(session)
        await wipe_lea(session, LEA_A)
        await wipe_lea(session, LEA_B)
        await session.commit()


async def _wipe_tenancy_operators(session: Any) -> None:
    subjects = [
        _SUBJECT_OPERATOR_A,
        _SUBJECT_CONNECTOR_ADMIN,
        _SUBJECT_FOUNDER_ADMIN,
    ]
    await session.execute(
        text(
            "DELETE FROM operator_lea_grant WHERE operator_id IN"
            " (SELECT id FROM operator WHERE subject = ANY(:s))"
            " OR granted_by IN"
            " (SELECT id FROM operator WHERE subject = ANY(:s))"
        ),
        {"s": subjects},
    )
    await session.execute(
        text(
            "DELETE FROM operator_role WHERE operator_id IN"
            " (SELECT id FROM operator WHERE subject = ANY(:s))"
            " OR granted_by IN"
            " (SELECT id FROM operator WHERE subject = ANY(:s))"
        ),
        {"s": subjects},
    )
    await session.execute(
        text("DELETE FROM operator WHERE subject = ANY(:s)"),
        {"s": subjects},
    )


def _authz_passed(status_code: int) -> bool:
    """Treat any non-403/401/404 as evidence that authz let the request through.

    A 409 from a state-refused service is fine: the authz layer ran
    and approved; the service then made its own call. The point of
    these tests is the authz boundary, not the service's
    preconditions.
    """

    return status_code not in (401, 403, 404)


# ── The eight cases the plan called out ─────────────────────────────────────


def test_operator_authorized_for_lea_a_can_retry_lea_a_sync(
    client: TestClient, tenancy_world: dict[str, Any]
) -> None:
    """An operator scoped to LEA A reaches the retry service for LEA A.

    Retry against a status='failed' sync_job hits the service's happy
    path; the response may be 200 or a service-level 409. Either way
    the authz layer let it through, which is what this test pins.
    """

    resp = client.post(
        f"/api/v1/syncs/{tenancy_world['sync_a']}/retry",
        headers=auth_header(tenancy_world["operator_a_jwt"]),
        json={"reason": "authz check, lea-a operator on lea-a sync"},
    )
    assert _authz_passed(resp.status_code), (
        f"Expected authz to pass (200 or 409), got {resp.status_code}"
        f" {resp.text}"
    )


def test_operator_authorized_for_lea_a_cannot_retry_lea_b_sync(
    client: TestClient, tenancy_world: dict[str, Any]
) -> None:
    """Cross-LEA retry is a 403, not a 404."""

    resp = client.post(
        f"/api/v1/syncs/{tenancy_world['sync_b']}/retry",
        headers=auth_header(tenancy_world["operator_a_jwt"]),
        json={"reason": "authz check, lea-a operator on lea-b sync"},
    )
    assert resp.status_code == 403
    assert LEA_B in resp.json()["detail"]


def test_operator_authorized_for_lea_a_cannot_revert_lea_b_sync(
    client: TestClient, tenancy_world: dict[str, Any]
) -> None:
    """Cross-LEA revert is a 403."""

    resp = client.post(
        f"/api/v1/syncs/{tenancy_world['sync_b']}/revert",
        headers=auth_header(tenancy_world["operator_a_jwt"]),
        json={"reason": "authz check, lea-a operator on lea-b revert"},
    )
    assert resp.status_code == 403


def test_operator_authorized_for_lea_a_cannot_release_lea_b_quarantine(
    client: TestClient, tenancy_world: dict[str, Any]
) -> None:
    """Cross-LEA quarantine release is a 403."""

    resp = client.post(
        f"/api/v1/quarantine/{tenancy_world['quar_b']}/release",
        headers=auth_header(tenancy_world["operator_a_jwt"]),
        json={},
    )
    assert resp.status_code == 403


def test_operator_authorized_for_lea_a_cannot_reject_lea_b_quarantine(
    client: TestClient, tenancy_world: dict[str, Any]
) -> None:
    """Cross-LEA quarantine reject is a 403."""

    resp = client.post(
        f"/api/v1/quarantine/{tenancy_world['quar_b']}/reject",
        headers=auth_header(tenancy_world["operator_a_jwt"]),
        json={"reason": "authz check, lea-a operator on lea-b reject"},
    )
    assert resp.status_code == 403


def test_owner_authorized_for_all_leas(
    client: TestClient, tenancy_world: dict[str, Any]
) -> None:
    """owner reaches every action endpoint on every LEA."""

    targets = [
        ("retry", f"/api/v1/syncs/{tenancy_world['sync_a']}/retry"),
        ("retry", f"/api/v1/syncs/{tenancy_world['sync_b']}/retry"),
        ("revert", f"/api/v1/syncs/{tenancy_world['sync_a']}/revert"),
        ("revert", f"/api/v1/syncs/{tenancy_world['sync_b']}/revert"),
        ("release", f"/api/v1/quarantine/{tenancy_world['quar_a']}/release"),
        ("release", f"/api/v1/quarantine/{tenancy_world['quar_b']}/release"),
        ("reject", f"/api/v1/quarantine/{tenancy_world['quar_a']}/reject"),
        ("reject", f"/api/v1/quarantine/{tenancy_world['quar_b']}/reject"),
    ]
    headers = auth_header(tenancy_world["owner_jwt"])
    for verb, path in targets:
        body = {"reason": f"owner authz check, {verb}"}
        resp = client.post(path, headers=headers, json=body)
        assert _authz_passed(resp.status_code), (
            f"owner should reach {path}, got"
            f" {resp.status_code} {resp.text}"
        )


def test_admin_authorized_for_all_leas(
    client: TestClient, tenancy_world: dict[str, Any]
) -> None:
    """admin satisfies the operator gate on every LEA."""

    headers = auth_header(tenancy_world["admin_jwt"])
    resp_a = client.post(
        f"/api/v1/syncs/{tenancy_world['sync_a']}/retry",
        headers=headers,
        json={"reason": "admin authz check, lea-a"},
    )
    resp_b = client.post(
        f"/api/v1/syncs/{tenancy_world['sync_b']}/retry",
        headers=headers,
        json={"reason": "admin authz check, lea-b"},
    )
    assert _authz_passed(resp_a.status_code)
    assert _authz_passed(resp_b.status_code)


def test_unknown_sync_job_id_returns_404_not_403(
    client: TestClient, tenancy_world: dict[str, Any]
) -> None:
    """An unknown id is 404 regardless of the operator's scope.

    The lookup raises 404 before the authz check, so an operator
    scoped to LEA A trying an id that does not exist anywhere sees
    a 404 (not a 403 that would leak that the id exists in another
    LEA).
    """

    unknown = uuid.uuid4()
    resp = client.post(
        f"/api/v1/syncs/{unknown}/retry",
        headers=auth_header(tenancy_world["operator_a_jwt"]),
        json={"reason": "authz check, unknown id"},
    )
    assert resp.status_code == 404
    assert str(unknown) in resp.json()["detail"]


# ── Extras that lock in the boundary symmetry ───────────────────────────────


def test_unknown_quarantine_id_returns_404_not_403(
    client: TestClient, tenancy_world: dict[str, Any]
) -> None:
    """Same info-disclosure rule on the quarantine endpoints."""

    unknown = uuid.uuid4()
    resp = client.post(
        f"/api/v1/quarantine/{unknown}/release",
        headers=auth_header(tenancy_world["operator_a_jwt"]),
        json={},
    )
    assert resp.status_code == 404
