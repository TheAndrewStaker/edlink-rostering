"""Operator-role result-set filtering on the three list endpoints.

The leaf-scope work in Session 16 (ADR-009) closed the per-LEA
drill-down. The list endpoints ``/leas``, ``/cursors``, and
``/quarantine`` still returned every LEA to every authenticated
reader, including operator-role users whose grant set covers only
one LEA. This test pins the operator-scope filter that S17-2
introduced.

Three invariants per endpoint:

1. ``auditor`` (implicit access to all active LEAs) sees both LEAs in
   the listing.
2. ``operator`` granted only LEA-A sees LEA-A and NOT LEA-B.
3. ``operator`` granted only LEA-A passing ``lea_id=LEA-B`` (where
   the endpoint supports the query parameter) gets an empty list,
   not a 403. The leaf-scope check applies to drill-down routes with
   ``:lea_id`` in the path; here the LEA id is a filter, and the
   info-disclosure-free posture is "empty result set."
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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


_LEA_A = LeaId("lea-list-scope-a")
_LEA_B = LeaId("lea-list-scope-b")


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
async def two_leas_with_data(
    db_session_factory: async_sessionmaker[Any],
) -> AsyncIterator[dict[str, Any]]:
    """Seeds LEA-A + LEA-B, an operator granted LEA-A, an auditor.

    Each LEA gets one cursor_state row and one unresolved quarantine
    row so all three list endpoints have something to filter.
    """

    ensure_test_secret()
    operator_subject = f"list-scope-op-{uuid.uuid4().hex[:8]}"
    auditor_subject = f"list-scope-auditor-{uuid.uuid4().hex[:8]}"
    op_id = uuid.uuid4()
    auditor_id = uuid.uuid4()
    now = datetime.now(UTC)

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
            await session.execute(
                text(
                    """
                    INSERT INTO cursor_state (
                        lea_id, partner, last_event_id, last_event_at,
                        last_poll_at, cold_start_required, updated_at
                    ) VALUES (
                        :lea, 'edlink', :evt, :seen, :now, false, :now
                    )
                    """
                ),
                {
                    "lea": lea_id,
                    "evt": f"evt_{lea_id}",
                    "seen": now - timedelta(hours=1),
                    "now": now,
                },
            )
            sync_id = uuid.uuid4()
            await session.execute(
                text(
                    """
                    INSERT INTO sync_jobs (
                        id, lea_id, partner, status, started_at,
                        completed_at, event_count
                    ) VALUES (
                        :id, :lea, 'edlink', 'success', :now, :now, 0
                    )
                    """
                ),
                {"id": sync_id, "lea": lea_id, "now": now},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO quarantine (
                        sync_job_id, lea_id, entity_type, entity_id,
                        reason, raw_payload, created_at
                    ) VALUES (
                        :sj, :lea, 'enrollment', :eid,
                        'Layer 4: orphan', CAST('{}' AS JSONB), :now
                    )
                    """
                ),
                {
                    "sj": sync_id,
                    "lea": lea_id,
                    "eid": f"enr-{lea_id}",
                    "now": now,
                },
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


# ── /leas ────────────────────────────────────────────────────────────────────


def test_auditor_sees_both_leas_in_list(
    client: TestClient, two_leas_with_data: dict[str, Any]
) -> None:
    r = client.get("/api/v1/leas", headers=two_leas_with_data["auditor_auth"])
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert _LEA_A in ids
    assert _LEA_B in ids


def test_operator_sees_only_granted_lea_in_list(
    client: TestClient, two_leas_with_data: dict[str, Any]
) -> None:
    r = client.get("/api/v1/leas", headers=two_leas_with_data["operator_auth"])
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert _LEA_A in ids
    assert _LEA_B not in ids


# ── /cursors ─────────────────────────────────────────────────────────────────


def test_auditor_sees_both_leas_in_cursors(
    client: TestClient, two_leas_with_data: dict[str, Any]
) -> None:
    r = client.get(
        "/api/v1/cursors", headers=two_leas_with_data["auditor_auth"]
    )
    assert r.status_code == 200, r.text
    ids = {row["lea_id"] for row in r.json()}
    assert _LEA_A in ids
    assert _LEA_B in ids


def test_operator_sees_only_granted_lea_in_cursors(
    client: TestClient, two_leas_with_data: dict[str, Any]
) -> None:
    r = client.get(
        "/api/v1/cursors", headers=two_leas_with_data["operator_auth"]
    )
    assert r.status_code == 200, r.text
    ids = {row["lea_id"] for row in r.json()}
    assert _LEA_A in ids
    assert _LEA_B not in ids


def test_operator_cursors_lea_id_query_outside_scope_returns_empty(
    client: TestClient, two_leas_with_data: dict[str, Any]
) -> None:
    """Filter by an LEA outside the operator's grant: empty list, not 403.

    The leaf-scope 403 applies to drill-down routes with ``:lea_id``
    in the path. Here ``lea_id`` is a query parameter (filter), so
    the info-disclosure-free shape is an empty result set.
    """

    r = client.get(
        f"/api/v1/cursors?lea_id={_LEA_B}",
        headers=two_leas_with_data["operator_auth"],
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


# ── /quarantine ──────────────────────────────────────────────────────────────


def test_auditor_sees_both_leas_in_quarantine(
    client: TestClient, two_leas_with_data: dict[str, Any]
) -> None:
    r = client.get(
        "/api/v1/quarantine", headers=two_leas_with_data["auditor_auth"]
    )
    assert r.status_code == 200, r.text
    ids = {row["lea_id"] for row in r.json()}
    assert _LEA_A in ids
    assert _LEA_B in ids


def test_operator_sees_only_granted_lea_in_quarantine(
    client: TestClient, two_leas_with_data: dict[str, Any]
) -> None:
    r = client.get(
        "/api/v1/quarantine", headers=two_leas_with_data["operator_auth"]
    )
    assert r.status_code == 200, r.text
    ids = {row["lea_id"] for row in r.json()}
    assert _LEA_A in ids
    assert _LEA_B not in ids


def test_operator_quarantine_lea_id_query_outside_scope_returns_empty(
    client: TestClient, two_leas_with_data: dict[str, Any]
) -> None:
    r = client.get(
        f"/api/v1/quarantine?lea_id={_LEA_B}",
        headers=two_leas_with_data["operator_auth"],
    )
    assert r.status_code == 200, r.text
    assert r.json() == []
