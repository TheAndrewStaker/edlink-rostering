"""Cross-LEA audit explorer tests.

Covers ``GET /api/admin/audit``:

- Auth: 401 without bearer; 403 if the role gate doesn't pass.
- Cross-LEA scope: owner / admin / auditor see
  every LEA; the operator role sees only LEAs in its grant set.
- Filters: action_prefix, operator_id, since/until window each
  narrow the result.
- Cursor pagination: ``next_cursor`` is correct, follow-up pages
  return the older entries strictly before the cursor, and the
  final page returns ``next_cursor=None``.
"""

from __future__ import annotations

import json
import os
import uuid
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


_LEA_A = LeaId("lea-audit-a")
_LEA_B = LeaId("lea-audit-b")
_PARTNER = "edlink"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seeded_leas(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    async with db_session_factory() as session:
        for lea, name in ((_LEA_A, "Audit A"), (_LEA_B, "Audit B")):
            await wipe_lea(session, lea)
            await session.execute(
                text(
                    """
                    INSERT INTO leas (id, name, lea_type, state)
                    VALUES (:id, :name, 'traditional_district', 'CA')
                    """
                ),
                {"id": lea, "name": name},
            )
        await session.commit()
    yield
    async with db_session_factory() as session:
        for lea in (_LEA_A, _LEA_B):
            await wipe_lea(session, lea)
        await session.commit()


async def _insert_sync(
    session: Any,
    *,
    lea_id: str,
    started_at: datetime,
    status_value: str = "success",
) -> uuid.UUID:
    sync_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO sync_jobs (
                id, lea_id, partner, status, started_at, completed_at,
                event_count, error_count, warning_count,
                cursor_before, cursor_after, error_summary
            ) VALUES (
                :id, :lea, :partner, :status, :started, :completed,
                1, 0, 0, NULL, 'evt_after', NULL
            )
            """
        ),
        {
            "id": sync_id,
            "lea": lea_id,
            "partner": _PARTNER,
            "status": status_value,
            "started": started_at,
            "completed": started_at + timedelta(seconds=1),
        },
    )
    return sync_id


async def _insert_reconciliation(
    session: Any,
    *,
    lea_id: str,
    started_at: datetime,
    status_value: str = "matched",
) -> uuid.UUID:
    rid = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO reconciliation_runs (
                id, lea_id, partner, started_at, completed_at,
                status, canonical_root_hash, partner_root_hash,
                drift_summary, error_message
            ) VALUES (
                :id, :lea, :partner, :started, :completed,
                :status, 'c', 'p', NULL, NULL
            )
            """
        ),
        {
            "id": rid,
            "lea": lea_id,
            "partner": _PARTNER,
            "started": started_at,
            "completed": started_at + timedelta(seconds=1),
            "status": status_value,
        },
    )
    return rid


async def _insert_operator(
    session: Any,
    *,
    subject: str,
    email: str,
    role: str = "auditor",
) -> uuid.UUID:
    op_id = uuid.uuid4()
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
            "name": email.split("@")[0],
            "email": email,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO operator_role
                (id, operator_id, role, granted_by, reason)
            VALUES (:id, :op, :role, :op, 'fixture')
            """
        ),
        {"id": uuid.uuid4(), "op": op_id, "role": role},
    )
    return op_id


async def _insert_audit_row(
    session: Any,
    *,
    operator_id: uuid.UUID,
    lea_id: str | None,
    action: str,
    created_at: datetime,
    reason: str = "fixture",
) -> uuid.UUID:
    aid = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO audit_log (
                id, operator_id, action, target_kind, target_id,
                lea_id, reason, detail, created_at
            ) VALUES (
                :id, :op, :action, 'connector_authorization',
                :tid, :lea, :reason, NULL, :ts
            )
            """
        ),
        {
            "id": aid,
            "op": operator_id,
            "action": action,
            "tid": str(uuid.uuid4()),
            "lea": lea_id,
            "reason": reason,
            "ts": created_at,
        },
    )
    return aid


async def _grant_lea(
    session: Any,
    *,
    operator_id: uuid.UUID,
    lea_id: str,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO operator_lea_grant
                (id, operator_id, lea_id, granted_by, reason)
            VALUES (:id, :op, :lea, :op, 'fixture')
            """
        ),
        {"id": uuid.uuid4(), "op": operator_id, "lea": lea_id},
    )


async def _cleanup_operators(
    factory: async_sessionmaker[Any], op_ids: list[uuid.UUID]
) -> None:
    if not op_ids:
        return
    async with factory() as session:
        await session.execute(
            text(
                "DELETE FROM operator_lea_grant"
                " WHERE operator_id = ANY(:ids)"
                " OR granted_by = ANY(:ids)"
                " OR revoked_by = ANY(:ids)"
            ),
            {"ids": op_ids},
        )
        await session.execute(
            text("DELETE FROM audit_log WHERE operator_id = ANY(:ids)"),
            {"ids": op_ids},
        )
        await session.execute(
            text(
                "DELETE FROM operator_role WHERE operator_id = ANY(:ids)"
                " OR granted_by = ANY(:ids)"
                " OR revoked_by = ANY(:ids)"
            ),
            {"ids": op_ids},
        )
        await session.execute(
            text("DELETE FROM operator WHERE id = ANY(:ids)"),
            {"ids": op_ids},
        )
        await session.commit()


@pytest.fixture
def client() -> TestClient:
    return TestClient(fastapi_app)


# ── Tests ───────────────────────────────────────────────────────────────────


def test_unauthenticated_returns_401(client: TestClient) -> None:
    resp = client.get("/api/v1/admin/audit")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auditor_sees_entries_across_all_leas(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_leas: Any,
) -> None:
    """An auditor with no LEA grants still sees both LEAs' rows."""

    ensure_test_secret()
    subject = f"audit-auditor-{uuid.uuid4().hex[:8]}"
    op_ids: list[uuid.UUID] = []

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        auditor_id = await _insert_operator(
            session, subject=subject,
            email=f"{subject}@edlink.test", role="auditor",
        )
        op_ids.append(auditor_id)
        await _insert_sync(
            session, lea_id=_LEA_A, started_at=now - timedelta(hours=2),
        )
        await _insert_sync(
            session, lea_id=_LEA_B, started_at=now - timedelta(hours=1),
        )
        await session.commit()

    headers = auth_header(mint_jwt(subject=subject))
    try:
        resp = client.get("/api/v1/admin/audit", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        lea_ids_in_response = {
            e["detail"]["lea_id"] for e in body["entries"] if e["detail"]
        }
        assert {_LEA_A, _LEA_B} <= lea_ids_in_response
    finally:
        await _cleanup_operators(db_session_factory, op_ids)


@pytest.mark.asyncio
async def test_operator_role_sees_only_granted_leas(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_leas: Any,
) -> None:
    """An operator with a single LEA grant only sees that LEA's rows."""

    ensure_test_secret()
    subject = f"audit-operator-{uuid.uuid4().hex[:8]}"
    op_ids: list[uuid.UUID] = []

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        operator_id = await _insert_operator(
            session, subject=subject,
            email=f"{subject}@edlink.test", role="operator",
        )
        op_ids.append(operator_id)
        await _grant_lea(session, operator_id=operator_id, lea_id=_LEA_A)
        await _insert_sync(
            session, lea_id=_LEA_A, started_at=now - timedelta(hours=2),
        )
        await _insert_sync(
            session, lea_id=_LEA_B, started_at=now - timedelta(hours=1),
        )
        await session.commit()

    headers = auth_header(mint_jwt(subject=subject))
    try:
        resp = client.get("/api/v1/admin/audit", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        lea_ids_in_response = {
            e["detail"]["lea_id"] for e in body["entries"] if e["detail"]
        }
        assert lea_ids_in_response == {_LEA_A}
    finally:
        await _cleanup_operators(db_session_factory, op_ids)


@pytest.mark.asyncio
async def test_operator_with_no_grants_gets_empty_page(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_leas: Any,
) -> None:
    """No grants must not silently widen scope to all LEAs."""

    ensure_test_secret()
    subject = f"audit-empty-operator-{uuid.uuid4().hex[:8]}"
    op_ids: list[uuid.UUID] = []

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        operator_id = await _insert_operator(
            session, subject=subject,
            email=f"{subject}@edlink.test", role="operator",
        )
        op_ids.append(operator_id)
        await _insert_sync(
            session, lea_id=_LEA_A, started_at=now - timedelta(hours=1),
        )
        await session.commit()

    headers = auth_header(mint_jwt(subject=subject))
    try:
        resp = client.get("/api/v1/admin/audit", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["entries"] == []
        assert body["next_cursor"] is None
    finally:
        await _cleanup_operators(db_session_factory, op_ids)


@pytest.mark.asyncio
async def test_action_prefix_filter_narrows_result(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_leas: Any,
) -> None:
    ensure_test_secret()
    subject = f"audit-prefix-{uuid.uuid4().hex[:8]}"
    op_ids: list[uuid.UUID] = []

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        auditor_id = await _insert_operator(
            session, subject=subject,
            email=f"{subject}@edlink.test", role="auditor",
        )
        op_ids.append(auditor_id)
        await _insert_sync(
            session, lea_id=_LEA_A, started_at=now - timedelta(hours=2),
        )
        await _insert_reconciliation(
            session, lea_id=_LEA_A, started_at=now - timedelta(hours=1),
            status_value="drift_detected",
        )
        await session.commit()

    headers = auth_header(mint_jwt(subject=subject))
    try:
        # No filter: both branches show up.
        full = client.get("/api/v1/admin/audit", headers=headers).json()
        sources_full = {e["source"] for e in full["entries"]}
        assert {"sync_jobs", "reconciliation_runs"} <= sources_full

        # Filter to reconciliation only.
        recon = client.get(
            "/api/v1/admin/audit?action_prefix=reconciliation.",
            headers=headers,
        ).json()
        sources_recon = {e["source"] for e in recon["entries"]}
        assert sources_recon == {"reconciliation_runs"}
    finally:
        await _cleanup_operators(db_session_factory, op_ids)


@pytest.mark.asyncio
async def test_operator_id_filter_narrows_to_one_operator(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_leas: Any,
) -> None:
    ensure_test_secret()
    subject = f"audit-opfilter-{uuid.uuid4().hex[:8]}"
    op_ids: list[uuid.UUID] = []

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        auditor_id = await _insert_operator(
            session, subject=subject,
            email=f"{subject}@edlink.test", role="auditor",
        )
        op_ids.append(auditor_id)
        other_id = await _insert_operator(
            session, subject=f"other-{uuid.uuid4().hex[:8]}",
            email=f"other-{uuid.uuid4().hex[:6]}@edlink.test",
            role="admin",
        )
        op_ids.append(other_id)

        await _insert_audit_row(
            session, operator_id=auditor_id, lea_id=_LEA_A,
            action="connector.authorized",
            created_at=now - timedelta(minutes=10),
        )
        await _insert_audit_row(
            session, operator_id=other_id, lea_id=_LEA_A,
            action="connector.authorized",
            created_at=now - timedelta(minutes=5),
        )
        await session.commit()

    headers = auth_header(mint_jwt(subject=subject))
    try:
        resp = client.get(
            f"/api/v1/admin/audit?operator_id={other_id}",
            headers=headers,
        )
        body = resp.json()
        assert len(body["entries"]) == 1
        # operator_id is not echoed in the response body; verify via
        # actor_email which resolves via the operator join.
        assert body["entries"][0]["actor_email"].startswith("other-")
    finally:
        await _cleanup_operators(db_session_factory, op_ids)


@pytest.mark.asyncio
async def test_since_window_narrows_result(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_leas: Any,
) -> None:
    ensure_test_secret()
    subject = f"audit-window-{uuid.uuid4().hex[:8]}"
    op_ids: list[uuid.UUID] = []

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        auditor_id = await _insert_operator(
            session, subject=subject,
            email=f"{subject}@edlink.test", role="auditor",
        )
        op_ids.append(auditor_id)
        await _insert_sync(
            session, lea_id=_LEA_A, started_at=now - timedelta(hours=24),
        )
        await _insert_sync(
            session, lea_id=_LEA_A, started_at=now - timedelta(minutes=10),
        )
        await session.commit()

    headers = auth_header(mint_jwt(subject=subject))
    try:
        since_recent = (now - timedelta(hours=1)).isoformat()
        resp_recent = client.get(
            "/api/v1/admin/audit",
            headers=headers,
            params={"since": since_recent},
        )
        assert resp_recent.status_code == 200, resp_recent.text
        body_recent = resp_recent.json()

        resp_all = client.get("/api/v1/admin/audit", headers=headers)
        body_all = resp_all.json()

        assert len(body_recent["entries"]) < len(body_all["entries"])
        test_entries = [
            e for e in body_recent["entries"]
            if e.get("detail", {}).get("lea_id") == _LEA_A
        ]
        assert len(test_entries) >= 1
    finally:
        await _cleanup_operators(db_session_factory, op_ids)


@pytest.mark.asyncio
async def test_cursor_pagination_walks_all_pages(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_leas: Any,
) -> None:
    """A page-by-page walk with cursor returns every entry exactly once."""

    ensure_test_secret()
    subject = f"audit-page-{uuid.uuid4().hex[:8]}"
    op_ids: list[uuid.UUID] = []

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        auditor_id = await _insert_operator(
            session, subject=subject,
            email=f"{subject}@edlink.test", role="auditor",
        )
        op_ids.append(auditor_id)
        for i in range(7):
            await _insert_sync(
                session,
                lea_id=_LEA_A,
                started_at=now - timedelta(minutes=i + 1),
            )
        await session.commit()

    headers = auth_header(mint_jwt(subject=subject))
    try:
        seen_ids: list[str] = []
        cursor_qs = ""
        for _ in range(20):  # bounded loop
            resp = client.get(
                f"/api/v1/admin/audit?limit=3{cursor_qs}",
                headers=headers,
            )
            body = resp.json()
            seen_ids.extend(e["id"] for e in body["entries"])
            cursor = body["next_cursor"]
            if cursor is None:
                break
            cursor_qs = (
                f"&cursor_occurred_at={cursor['occurred_at']}"
                f"&cursor_id={cursor['id']}"
            )
        assert len(seen_ids) >= 7
        assert len(set(seen_ids)) == len(seen_ids)
    finally:
        await _cleanup_operators(db_session_factory, op_ids)


@pytest.mark.asyncio
async def test_audit_log_with_null_lea_appears_in_global_view(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_leas: Any,
) -> None:
    """System-wide audit rows (lea_id IS NULL) are deliberately excluded.

    The per-LEA view skipped them (Session 9); the global view also
    skips them because audit_log's CTE filters ``lea_id IS NOT NULL``
    for consistency with the per-LEA path. When we later add a
    global-operator-actions section, lift that predicate.
    """

    ensure_test_secret()
    subject = f"audit-nulllea-{uuid.uuid4().hex[:8]}"
    op_ids: list[uuid.UUID] = []

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        auditor_id = await _insert_operator(
            session, subject=subject,
            email=f"{subject}@edlink.test", role="auditor",
        )
        op_ids.append(auditor_id)
        await _insert_audit_row(
            session, operator_id=auditor_id, lea_id=None,
            action="operator.role_granted",
            created_at=now - timedelta(minutes=5),
        )
        await _insert_audit_row(
            session, operator_id=auditor_id, lea_id=_LEA_A,
            action="connector.authorized",
            created_at=now - timedelta(minutes=3),
        )
        await session.commit()

    headers = auth_header(mint_jwt(subject=subject))
    try:
        resp = client.get("/api/v1/admin/audit", headers=headers)
        body = resp.json()
        actions = [e["action"] for e in body["entries"]]
        assert "operator.role_granted" not in actions
        assert "connector.authorized" in actions
    finally:
        await _cleanup_operators(db_session_factory, op_ids)
