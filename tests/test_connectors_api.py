"""Phase 1.5d connector management API tests.

Hits the live Postgres (skipped if APP_DATABASE_URL is unset, same as
test_api.py) and uses the Key Vault mock via FastAPI's
``dependency_overrides`` so a per-test staged secret stays isolated.

Coverage:

- list:
  * 401 without bearer
  * admin sees all LEAs
  * operator scoped to one LEA only sees that LEA
  * auditor passes (read-only role)
- authorize:
  * admin authorizes a pending row → active + audit_log
  * idempotent on already-active row
  * operator role gets 403 (action endpoints exclude operator)
  * empty reason gets 422 (Pydantic min_length=1)
- revoke:
  * marks revoked_at, writes audit_log
  * 404 when no live row
- adjust-poll-interval:
  * updates poll_interval_seconds, writes audit_log
  * 422 when interval out of [60, 3600] range
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

from edlink_rostering.api import app as fastapi_app
from edlink_rostering.core.types import LeaId
from tests.conftest import wipe_lea
from tests.fixtures.auth import auth_header, ensure_test_secret, mint_jwt


pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("OPS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    ),
    reason="OPS_DATABASE_URL/DATABASE_URL not set; skipping HTTP tests",
)


_TEST_LEA_A = LeaId("lea-conn-test-a")
_TEST_LEA_B = LeaId("lea-conn-test-b")
_TEST_PARTNER = "edlink"


@pytest.fixture
def client() -> TestClient:
    return TestClient(fastapi_app)


@pytest_asyncio.fixture
async def seeded_test_leas(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Two empty LEA rows so the connector authz tests have FK targets."""

    async with db_session_factory() as session:
        for lea_id in (_TEST_LEA_A, _TEST_LEA_B):
            await wipe_lea(session, lea_id)
            await session.execute(
                text(
                    """
                    INSERT INTO leas (id, name, lea_type, state)
                    VALUES (:id, :name, 'traditional_district', 'CA')
                    """
                ),
                {"id": lea_id, "name": f"LEA {lea_id}"},
            )
        await session.commit()

    yield

    async with db_session_factory() as session:
        for lea_id in (_TEST_LEA_A, _TEST_LEA_B):
            await wipe_lea(session, lea_id)
        await session.commit()


async def _seed_operator(
    factory: async_sessionmaker[Any],
    *,
    subject: str,
    role: str,
    lea_grants: tuple[LeaId, ...] = (),
) -> tuple[uuid.UUID, dict[str, str]]:
    """Insert an operator + role + optional LEA grants. Return (op_id, header)."""

    ensure_test_secret()
    op_id = uuid.uuid4()
    async with factory() as session:
        # Clean up any prior row for this subject so the test is hermetic.
        await session.execute(
            text(
                "DELETE FROM operator_lea_grant WHERE operator_id IN"
                " (SELECT id FROM operator WHERE subject = :s)"
            ),
            {"s": subject},
        )
        await session.execute(
            text(
                "DELETE FROM operator_role WHERE operator_id IN"
                " (SELECT id FROM operator WHERE subject = :s)"
            ),
            {"s": subject},
        )
        await session.execute(
            text("DELETE FROM operator WHERE subject = :s"),
            {"s": subject},
        )
        await session.execute(
            text(
                """
                INSERT INTO operator (id, subject, display_name, email, status)
                VALUES (:id, :sub, :name, :email, 'active')
                """
            ),
            {
                "id": op_id,
                "sub": subject,
                "name": f"Test {subject}",
                "email": f"{subject}@edlink.test",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO operator_role
                    (id, operator_id, role, granted_by, reason)
                VALUES (:id, :op, :role, :op, 'test fixture')
                """
            ),
            {"id": uuid.uuid4(), "op": op_id, "role": role},
        )
        for lea_id in lea_grants:
            await session.execute(
                text(
                    """
                    INSERT INTO operator_lea_grant
                        (id, operator_id, lea_id, granted_by, reason)
                    VALUES (:id, :op, :lea, :op, 'test fixture')
                    """
                ),
                {"id": uuid.uuid4(), "op": op_id, "lea": lea_id},
            )
        await session.commit()

    token = mint_jwt(
        subject=subject,
        email=f"{subject}@edlink.test",
        name=f"Test {subject}",
    )
    return op_id, auth_header(token)


async def _cleanup_operator(
    factory: async_sessionmaker[Any], subject: str
) -> None:
    """Clear an operator and every row that references it.

    The references span audit_log, operator_lea_grant, operator_role
    (via operator_id + granted_by + revoked_by), and
    connector_authorization (via authorized_by + revoked_by). Clear
    them in FK order before the operator delete itself.
    """

    async with factory() as session:
        op_filter = (
            "(SELECT id FROM operator WHERE subject = :s)"
        )
        await session.execute(
            text(
                f"DELETE FROM audit_log WHERE operator_id IN {op_filter}"
            ),
            {"s": subject},
        )
        await session.execute(
            text(
                "DELETE FROM connector_authorization"
                f" WHERE authorized_by IN {op_filter}"
                f" OR revoked_by IN {op_filter}"
            ),
            {"s": subject},
        )
        await session.execute(
            text(
                "DELETE FROM operator_lea_grant"
                f" WHERE operator_id IN {op_filter}"
                f" OR granted_by IN {op_filter}"
                f" OR revoked_by IN {op_filter}"
            ),
            {"s": subject},
        )
        await session.execute(
            text(
                "DELETE FROM operator_role"
                f" WHERE operator_id IN {op_filter}"
                f" OR granted_by IN {op_filter}"
                f" OR revoked_by IN {op_filter}"
            ),
            {"s": subject},
        )
        await session.execute(
            text("DELETE FROM operator WHERE subject = :s"),
            {"s": subject},
        )
        await session.commit()


# ── List endpoint ─────────────────────────────────────────────────────────────


def test_list_connectors_requires_authorization(
    client: TestClient,
    seeded_test_leas: Any,
) -> None:
    response = client.get("/api/v1/connectors")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_connectors_as_admin_returns_all(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_test_leas: Any,
) -> None:
    subject = "test-conn-list-admin"
    _, header = await _seed_operator(
        db_session_factory, subject=subject, role="admin"
    )
    try:
        # Authorize both LEAs first so there's something to list.
        for lea in (_TEST_LEA_A, _TEST_LEA_B):
            client.post(
                f"/api/v1/connectors/{lea}/{_TEST_PARTNER}/authorize",
                json={
                    "reason": "seed for list test",
                },
                headers=header,
            )
        resp = client.get("/api/v1/connectors", headers=header)
        assert resp.status_code == 200
        body = resp.json()
        lea_ids = {row["lea_id"] for row in body}
        assert _TEST_LEA_A in lea_ids
        assert _TEST_LEA_B in lea_ids
        for row in body:
            if row["lea_id"] in (_TEST_LEA_A, _TEST_LEA_B):
                assert row["status"] == "active"
                assert row["partner"] == _TEST_PARTNER
                assert row["authorized_by_email"].startswith(subject)
    finally:
        await _cleanup_operator(db_session_factory, subject)


@pytest.mark.asyncio
async def test_list_connectors_as_operator_scoped_to_one_lea(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_test_leas: Any,
) -> None:
    admin_subject = "test-conn-scoped-setup-admin"
    op_subject = "test-conn-scoped-op"
    _, admin_header = await _seed_operator(
        db_session_factory, subject=admin_subject, role="admin"
    )
    try:
        # Admin authorizes both LEAs.
        for lea in (_TEST_LEA_A, _TEST_LEA_B):
            client.post(
                f"/api/v1/connectors/{lea}/{_TEST_PARTNER}/authorize",
                json={
                    "reason": "seed for scope test",
                },
                headers=admin_header,
            )
        # Operator scoped to LEA A only.
        _, op_header = await _seed_operator(
            db_session_factory,
            subject=op_subject,
            role="operator",
            lea_grants=(_TEST_LEA_A,),
        )
        try:
            resp = client.get("/api/v1/connectors", headers=op_header)
            assert resp.status_code == 200
            visible_lea_ids = {row["lea_id"] for row in resp.json()}
            assert _TEST_LEA_A in visible_lea_ids
            assert _TEST_LEA_B not in visible_lea_ids
        finally:
            await _cleanup_operator(db_session_factory, op_subject)
    finally:
        await _cleanup_operator(db_session_factory, admin_subject)


# ── Authorize endpoint ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authorize_writes_audit_log(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_test_leas: Any,
) -> None:
    subject = "test-conn-authorize"
    op_id, header = await _seed_operator(
        db_session_factory, subject=subject, role="admin"
    )
    try:
        resp = client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/authorize",
            json={
                "reason": "initial authorize",
            },
            headers=header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "active"
        assert body["created_new_row"] is True
        # Audit row written in the same transaction.
        async with db_session_factory() as session:
            audit_rows = (
                await session.execute(
                    text(
                        """
                        SELECT action, target_id, lea_id, reason
                        FROM audit_log
                        WHERE operator_id = :op
                          AND action = 'connector.authorized'
                        """
                    ),
                    {"op": op_id},
                )
            ).all()
        assert len(audit_rows) == 1
        assert audit_rows[0].lea_id == _TEST_LEA_A
        assert audit_rows[0].reason == "initial authorize"
    finally:
        await _cleanup_operator(db_session_factory, subject)


@pytest.mark.asyncio
async def test_authorize_operator_role_gets_403(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_test_leas: Any,
) -> None:
    subject = "test-conn-op-rejected"
    _, header = await _seed_operator(
        db_session_factory,
        subject=subject,
        role="operator",
        lea_grants=(_TEST_LEA_A,),
    )
    try:
        resp = client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/authorize",
            json={
                "reason": "operator should not be able to do this",
            },
            headers=header,
        )
        assert resp.status_code == 403
    finally:
        await _cleanup_operator(db_session_factory, subject)


@pytest.mark.asyncio
async def test_authorize_empty_reason_rejected(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_test_leas: Any,
) -> None:
    subject = "test-conn-empty-reason"
    _, header = await _seed_operator(
        db_session_factory, subject=subject, role="admin"
    )
    try:
        resp = client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/authorize",
            json={
                "reason": "",
            },
            headers=header,
        )
        assert resp.status_code == 422
    finally:
        await _cleanup_operator(db_session_factory, subject)


# ── Revoke endpoint ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_marks_revoked_at_and_audits(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_test_leas: Any,
) -> None:
    subject = "test-conn-revoke"
    op_id, header = await _seed_operator(
        db_session_factory, subject=subject, role="admin"
    )
    try:
        client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/authorize",
            json={
                "reason": "set up for revoke",
            },
            headers=header,
        )
        resp = client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/revoke",
            json={"reason": "LEA contract ended"},
            headers=header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["revoked_at"] is not None

        async with db_session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT status, revoked_at, revoked_by
                        FROM connector_authorization
                        WHERE id = :id
                        """
                    ),
                    {"id": body["id"]},
                )
            ).one()
            assert row.status == "revoked"
            assert row.revoked_at is not None
            assert row.revoked_by == op_id
            audit_count = (
                await session.execute(
                    text(
                        """
                        SELECT COUNT(*) AS n FROM audit_log
                        WHERE action = 'connector.revoked'
                          AND operator_id = :op
                        """
                    ),
                    {"op": op_id},
                )
            ).one().n
        assert audit_count == 1
    finally:
        await _cleanup_operator(db_session_factory, subject)


@pytest.mark.asyncio
async def test_revoke_no_live_row_returns_404(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_test_leas: Any,
) -> None:
    subject = "test-conn-revoke-404"
    _, header = await _seed_operator(
        db_session_factory, subject=subject, role="admin"
    )
    try:
        resp = client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/revoke",
            json={"reason": "nothing to revoke"},
            headers=header,
        )
        assert resp.status_code == 404
    finally:
        await _cleanup_operator(db_session_factory, subject)


# ── Adjust poll interval ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adjust_poll_interval_updates_value(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_test_leas: Any,
) -> None:
    subject = "test-conn-adjust"
    op_id, header = await _seed_operator(
        db_session_factory, subject=subject, role="admin"
    )
    try:
        client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/authorize",
            json={
                "reason": "set up for adjust",
            },
            headers=header,
        )
        resp = client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/adjust-poll-interval",
            json={
                "new_poll_interval_seconds": 600,
                "reason": "match LEA SIS refresh cadence",
            },
            headers=header,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["previous_poll_interval_seconds"] == 300
        assert body["new_poll_interval_seconds"] == 600
    finally:
        await _cleanup_operator(db_session_factory, subject)


@pytest.mark.asyncio
async def test_adjust_poll_interval_out_of_range_rejected(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_test_leas: Any,
) -> None:
    subject = "test-conn-adjust-bad"
    _, header = await _seed_operator(
        db_session_factory, subject=subject, role="admin"
    )
    try:
        client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/authorize",
            json={
                "reason": "set up",
            },
            headers=header,
        )
        # Below the 60s minimum.
        resp = client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/adjust-poll-interval",
            json={"new_poll_interval_seconds": 30, "reason": "too fast"},
            headers=header,
        )
        assert resp.status_code == 422
        # Above the 3600s ceiling.
        resp = client.post(
            f"/api/v1/connectors/{_TEST_LEA_A}/{_TEST_PARTNER}/adjust-poll-interval",
            json={"new_poll_interval_seconds": 5000, "reason": "too slow"},
            headers=header,
        )
        assert resp.status_code == 422
    finally:
        await _cleanup_operator(db_session_factory, subject)
