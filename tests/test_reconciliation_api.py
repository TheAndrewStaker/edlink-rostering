"""Reconciliation history API tests.

Covers ``GET /api/leas/{lea_id}/reconciliation``:

- 401 without bearer token
- auditor role passes the gate (read endpoint)
- ordering is newest-first by started_at
- limit caps the response
- drift_summary is unpacked into the typed list of per-entity drift
- empty list for an LEA with no runs (no 404)
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


_TEST_LEA = LeaId("lea-recon-api-test")
_PARTNER = "edlink"


@pytest.fixture
def client() -> TestClient:
    return TestClient(fastapi_app)


@pytest_asyncio.fixture
async def seeded_lea(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    async with db_session_factory() as session:
        await wipe_lea(session, _TEST_LEA)
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'Recon API Test', 'traditional_district', 'CA')
                """
            ),
            {"id": _TEST_LEA},
        )
        await session.commit()

    yield

    async with db_session_factory() as session:
        await wipe_lea(session, _TEST_LEA)
        await session.commit()


async def _insert_run(
    session: Any,
    *,
    completed_at: datetime,
    status_value: str,
    drift_summary: list[dict[str, Any]] | None,
    canonical_root: str = "c-hash",
    partner_root: str | None = "p-hash",
) -> uuid.UUID:
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
            "lea": _TEST_LEA,
            "partner": _PARTNER,
            "started": completed_at - timedelta(seconds=5),
            "completed": completed_at,
            "status": status_value,
            "canonical": canonical_root,
            "partner_hash": partner_root,
            "drift": json.dumps(drift_summary) if drift_summary else None,
        },
    )
    return run_id


async def _seed_auditor(
    factory: async_sessionmaker[Any],
    *,
    subject: str,
) -> dict[str, str]:
    ensure_test_secret()
    op_id = uuid.uuid4()
    async with factory() as session:
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
                VALUES (:id, :op, 'auditor', :op, 'test fixture')
                """
            ),
            {"id": uuid.uuid4(), "op": op_id},
        )
        await session.commit()
    token = mint_jwt(subject=subject)
    return auth_header(token)


async def _cleanup_operator(
    factory: async_sessionmaker[Any], subject: str
) -> None:
    async with factory() as session:
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
        await session.commit()


def test_unauthenticated_request_returns_401(client: TestClient) -> None:
    resp = client.get(f"/api/v1/leas/{_TEST_LEA}/reconciliation")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auditor_can_list_recent_runs_newest_first(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    subject = f"auditor-{uuid.uuid4().hex[:8]}"
    headers = await _seed_auditor(db_session_factory, subject=subject)

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        oldest = await _insert_run(
            session,
            completed_at=now - timedelta(hours=10),
            status_value="matched",
            drift_summary=None,
        )
        middle = await _insert_run(
            session,
            completed_at=now - timedelta(hours=4),
            status_value="drift_detected",
            drift_summary=[
                {
                    "entity_type": "students",
                    "canonical_only_ids": ["stu-only"],
                    "partner_only_ids": [],
                    "canonical_mid_hash": "c-mid",
                    "partner_mid_hash": "p-mid",
                }
            ],
        )
        newest = await _insert_run(
            session,
            completed_at=now - timedelta(minutes=10),
            status_value="matched",
            drift_summary=None,
        )
        await session.commit()

    try:
        resp = client.get(
            f"/api/v1/leas/{_TEST_LEA}/reconciliation", headers=headers
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert [r["id"] for r in rows] == [
            str(newest),
            str(middle),
            str(oldest),
        ]
        drift_row = next(r for r in rows if r["id"] == str(middle))
        assert drift_row["status"] == "drift_detected"
        assert len(drift_row["drift"]) == 1
        d = drift_row["drift"][0]
        assert d["entity_type"] == "students"
        assert d["canonical_only_ids"] == ["stu-only"]
        assert d["partner_only_ids"] == []
        assert d["canonical_mid_hash"] == "c-mid"
        assert d["partner_mid_hash"] == "p-mid"
    finally:
        await _cleanup_operator(db_session_factory, subject)


@pytest.mark.asyncio
async def test_limit_caps_the_response(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    subject = f"auditor-{uuid.uuid4().hex[:8]}"
    headers = await _seed_auditor(db_session_factory, subject=subject)

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        for i in range(5):
            await _insert_run(
                session,
                completed_at=now - timedelta(hours=i + 1),
                status_value="matched",
                drift_summary=None,
            )
        await session.commit()

    try:
        resp = client.get(
            f"/api/v1/leas/{_TEST_LEA}/reconciliation?limit=2",
            headers=headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2
    finally:
        await _cleanup_operator(db_session_factory, subject)


@pytest.mark.asyncio
async def test_empty_history_returns_empty_list_not_404(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    """LEA exists but has no reconciliation_runs rows yet."""

    subject = f"auditor-{uuid.uuid4().hex[:8]}"
    headers = await _seed_auditor(db_session_factory, subject=subject)

    try:
        resp = client.get(
            f"/api/v1/leas/{_TEST_LEA}/reconciliation", headers=headers
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        await _cleanup_operator(db_session_factory, subject)


@pytest.mark.asyncio
async def test_unknown_lea_returns_empty_list_not_404(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
) -> None:
    """No info disclosure on LEA existence; mirrors /leas/{id}/syncs."""

    subject = f"auditor-{uuid.uuid4().hex[:8]}"
    headers = await _seed_auditor(db_session_factory, subject=subject)

    try:
        resp = client.get(
            "/api/v1/leas/lea-does-not-exist/reconciliation", headers=headers
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        await _cleanup_operator(db_session_factory, subject)
