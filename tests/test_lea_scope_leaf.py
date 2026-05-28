"""Leaf-level LEA scope-check tests.

The per-LEA drill-down endpoints (``/api/leas/{lea_id}/timeline``,
``/api/leas/{lea_id}/reconciliation``) used to rely on the LEA list
endpoint having pre-filtered, so an operator scoped to LEA-A could
guess a LEA-B id and read its data. P1.5 promotes the scope check
into the dependency stack via ``require_lea_scope_at``; this test
file verifies the new boundary.

Three invariants:

1. An ``auditor`` role (implicit access to all active LEAs) reaches
   any LEA drill-down without 403, including unknown LEA ids that
   return the existing empty-list info-disclosure-free shape.
2. An ``operator`` role hitting a LEA it has been granted scope to
   receives a 200 response.
3. An ``operator`` role hitting a LEA it has NOT been granted scope to
   receives a 403 ProblemDetail.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
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


_LEA_A = LeaId("lea-scope-a")
_LEA_B = LeaId("lea-scope-b")


@pytest.fixture
def client() -> TestClient:
    return TestClient(fastapi_app)


async def _wipe_op(factory: async_sessionmaker[Any], subject: str) -> None:
    async with factory() as session:
        op_filter = "(SELECT id FROM operator WHERE subject = :s)"
        for stmt in (
            f"DELETE FROM audit_log WHERE operator_id IN {op_filter}",
            f"DELETE FROM idempotency_keys WHERE operator_id IN {op_filter}",
            f"DELETE FROM operator_lea_grant WHERE operator_id IN {op_filter}",
            f"DELETE FROM operator_role WHERE operator_id IN {op_filter}",
            "DELETE FROM operator WHERE subject = :s",
        ):
            await session.execute(text(stmt), {"s": subject})
        await session.commit()


@pytest_asyncio.fixture
async def two_leas_and_operators(
    db_session_factory: async_sessionmaker[Any],
) -> AsyncIterator[dict[str, Any]]:
    """Seeds LEA-A, LEA-B, plus an operator granted only LEA-A."""

    ensure_test_secret()
    operator_subject = f"scope-op-{uuid.uuid4().hex[:8]}"
    auditor_subject = f"scope-auditor-{uuid.uuid4().hex[:8]}"
    op_id = uuid.uuid4()
    auditor_id = uuid.uuid4()

    async with db_session_factory() as session:
        await wipe_lea(session, _LEA_A)
        await wipe_lea(session, _LEA_B)
        for lea_id in (_LEA_A, _LEA_B):
            await session.execute(
                text(
                    """
                    INSERT INTO leas (id, name, lea_type, state)
                    VALUES (:id, :name, 'traditional_district', 'CA')
                    """
                ),
                {"id": lea_id, "name": f"LEA {lea_id}"},
            )
        for subj, oid, role in (
            (operator_subject, op_id, "operator"),
            (auditor_subject, auditor_id, "auditor"),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO operator
                        (id, subject, display_name, email, status)
                    VALUES
                        (:id, :sub, :name, :email, 'active')
                    """
                ),
                {
                    "id": oid,
                    "sub": subj,
                    "name": f"Test {subj}",
                    "email": f"{subj}@edlink.test",
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
                {"id": uuid.uuid4(), "op": oid, "role": role},
            )
        # Operator gets explicit grant for LEA-A only.
        await session.execute(
            text(
                """
                INSERT INTO operator_lea_grant
                    (id, operator_id, lea_id, granted_by, reason)
                VALUES (:id, :op, :lea, :op, 'test fixture')
                """
            ),
            {"id": uuid.uuid4(), "op": op_id, "lea": _LEA_A},
        )
        await session.commit()

    yield {
        "operator_auth": auth_header(mint_jwt(subject=operator_subject)),
        "auditor_auth": auth_header(mint_jwt(subject=auditor_subject)),
    }

    async with db_session_factory() as session:
        await wipe_lea(session, _LEA_A)
        await wipe_lea(session, _LEA_B)
        await session.commit()
    await _wipe_op(db_session_factory, operator_subject)
    await _wipe_op(db_session_factory, auditor_subject)


def test_auditor_can_read_any_lea_drilldown(
    client: TestClient, two_leas_and_operators: dict[str, Any]
) -> None:
    """Auditor's implicit-all scope reaches both LEAs."""

    a = client.get(
        f"/api/v1/leas/{_LEA_A}/reconciliation",
        headers=two_leas_and_operators["auditor_auth"],
    )
    b = client.get(
        f"/api/v1/leas/{_LEA_B}/reconciliation",
        headers=two_leas_and_operators["auditor_auth"],
    )
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text


def test_auditor_unknown_lea_returns_empty_not_403(
    client: TestClient, two_leas_and_operators: dict[str, Any]
) -> None:
    """Auditor + unknown LEA preserves the empty-list info-disclosure-free shape."""

    r = client.get(
        "/api/v1/leas/lea-does-not-exist/reconciliation",
        headers=two_leas_and_operators["auditor_auth"],
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_operator_in_scope_reaches_endpoint(
    client: TestClient, two_leas_and_operators: dict[str, Any]
) -> None:
    """Operator with grant for LEA-A reaches LEA-A's drill-down."""

    r = client.get(
        f"/api/v1/leas/{_LEA_A}/timeline",
        headers=two_leas_and_operators["operator_auth"],
    )
    assert r.status_code == 200, r.text


def test_operator_out_of_scope_is_403(
    client: TestClient, two_leas_and_operators: dict[str, Any]
) -> None:
    """Operator without grant for LEA-B is blocked at the leaf."""

    r = client.get(
        f"/api/v1/leas/{_LEA_B}/timeline",
        headers=two_leas_and_operators["operator_auth"],
    )
    assert r.status_code == 403, r.text
    payload = r.json()
    assert payload["status"] == 403
    assert _LEA_B in payload["detail"]
