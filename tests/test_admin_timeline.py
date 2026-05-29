"""Admin timeline tests.

Covers the per-LEA timeline UNION (``services/admin_timeline.py``)
plus the HTTP surface (``GET /api/leas/{lea_id}/timeline``).

The service test drives Postgres directly so each of the six UNION
branches is exercised. The HTTP tests cover the auth gate, ordering,
and the empty-LEA contract that mirrors the other per-LEA endpoints.
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
from edlink_rostering.services.admin_timeline import list_timeline_for_lea
from tests.conftest import wipe_lea
from tests.fixtures.auth import auth_header, ensure_test_secret, mint_jwt


pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("OPS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    ),
    reason="OPS_DATABASE_URL/DATABASE_URL not set; skipping DB-bound tests",
)


_TEST_LEA = LeaId("lea-timeline-test")
_PARTNER = "edlink"


# ── Seed helpers ────────────────────────────────────────────────────────────


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
                VALUES (:id, 'Timeline Test', 'traditional_district', 'CA')
                """
            ),
            {"id": _TEST_LEA},
        )
        await session.commit()
    yield
    async with db_session_factory() as session:
        await wipe_lea(session, _TEST_LEA)
        await session.commit()


async def _insert_sync(
    session: Any,
    *,
    started_at: datetime,
    status_value: str = "success",
    event_count: int = 1,
    error_summary: str | None = None,
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
                :ec, 0, 0, NULL, 'evt_after', :err
            )
            """
        ),
        {
            "id": sync_id,
            "lea": _TEST_LEA,
            "partner": _PARTNER,
            "status": status_value,
            "started": started_at,
            "completed": started_at + timedelta(seconds=2),
            "ec": event_count,
            "err": error_summary,
        },
    )
    return sync_id


async def _insert_revert(
    session: Any,
    *,
    sync_job_id: uuid.UUID,
    reverted_at: datetime,
    operator: str = "ops@edlink.test",
    reason: str = "rollback bad batch",
    snapshots_restored: int = 3,
) -> uuid.UUID:
    rid = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO revert_actions (
                id, sync_job_id, revert_generation_id, operator_identity,
                reason, reverted_at, snapshots_restored
            ) VALUES (
                :id, :sj, :rg, :op, :reason, :ts, :sr
            )
            """
        ),
        {
            "id": rid,
            "sj": sync_job_id,
            "rg": uuid.uuid4(),
            "op": operator,
            "reason": reason,
            "ts": reverted_at,
            "sr": snapshots_restored,
        },
    )
    return rid


async def _insert_retry(
    session: Any,
    *,
    sync_job_id: uuid.UUID,
    retried_at: datetime,
    operator: str = "ops@edlink.test",
    reason: str = "transient partner 502",
    forced: bool = False,
) -> uuid.UUID:
    rid = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO retry_actions (
                id, sync_job_id, lea_id, partner, operator_identity,
                reason, retried_at, cursor_rewound_to, forced
            ) VALUES (
                :id, :sj, :lea, :partner, :op, :reason, :ts, 'evt_before', :forced
            )
            """
        ),
        {
            "id": rid,
            "sj": sync_job_id,
            "lea": _TEST_LEA,
            "partner": _PARTNER,
            "op": operator,
            "reason": reason,
            "ts": retried_at,
            "forced": forced,
        },
    )
    return rid


async def _insert_quarantine(
    session: Any,
    *,
    sync_job_id: uuid.UUID,
    created_at: datetime,
    entity_id: str,
    resolved_at: datetime | None = None,
    resolution_status: str | None = None,
    resolution_operator: str | None = None,
) -> uuid.UUID:
    qid = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO quarantine (
                id, sync_job_id, lea_id, entity_type, entity_id,
                reason, raw_payload, created_at, resolved_at,
                resolution_status, resolution_operator
            ) VALUES (
                :id, :sj, :lea, 'enrollments', :eid,
                'ENROLLMENT_ORPHAN_STUDENT', CAST('{}' AS JSONB),
                :cts, :rts, :rstatus, :rop
            )
            """
        ),
        {
            "id": qid,
            "sj": sync_job_id,
            "lea": _TEST_LEA,
            "eid": entity_id,
            "cts": created_at,
            "rts": resolved_at,
            "rstatus": resolution_status,
            "rop": resolution_operator,
        },
    )
    return qid


async def _insert_reconciliation(
    session: Any,
    *,
    started_at: datetime,
    status_value: str = "matched",
    drift_summary: list[dict[str, Any]] | None = None,
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
                :status, 'c-hash', 'p-hash',
                CAST(:drift AS JSONB), NULL
            )
            """
        ),
        {
            "id": rid,
            "lea": _TEST_LEA,
            "partner": _PARTNER,
            "started": started_at,
            "completed": started_at + timedelta(seconds=3),
            "status": status_value,
            "drift": json.dumps(drift_summary) if drift_summary else None,
        },
    )
    return rid


async def _insert_operator(
    session: Any,
    *,
    subject: str,
    email: str,
    role: str = "admin",
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


async def _insert_audit_log(
    session: Any,
    *,
    operator_id: uuid.UUID,
    action: str,
    created_at: datetime,
    target_id: str | None = None,
    reason: str = "fixture audit row",
    detail: dict[str, Any] | None = None,
) -> uuid.UUID:
    aid = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO audit_log (
                id, operator_id, action, target_kind, target_id,
                lea_id, reason, detail, created_at
            ) VALUES (
                :id, :op, :action, 'connector_authorization', :tid,
                :lea, :reason, CAST(:detail AS JSONB), :ts
            )
            """
        ),
        {
            "id": aid,
            "op": operator_id,
            "action": action,
            "tid": target_id or str(uuid.uuid4()),
            "lea": _TEST_LEA,
            "reason": reason,
            "detail": json.dumps(detail) if detail else None,
            "ts": created_at,
        },
    )
    return aid


async def _cleanup_operator(
    factory: async_sessionmaker[Any], op_id: uuid.UUID
) -> None:
    async with factory() as session:
        await session.execute(
            text("DELETE FROM audit_log WHERE operator_id = :op"),
            {"op": op_id},
        )
        await session.execute(
            text("DELETE FROM operator_role WHERE operator_id = :op"),
            {"op": op_id},
        )
        await session.execute(
            text("DELETE FROM operator WHERE id = :op"), {"op": op_id}
        )
        await session.commit()


# ── Service-layer tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_unions_all_six_sources_newest_first(
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    """One event per source, then verify ordering and per-source fields."""

    now = datetime.now(UTC)
    op_subject = f"timeline-op-{uuid.uuid4().hex[:8]}"
    op_email = f"{op_subject}@edlink.test"

    async with db_session_factory() as session:
        # t1 (oldest): the sync_job that everything else hangs off of
        sync_id = await _insert_sync(
            session,
            started_at=now - timedelta(hours=12),
            status_value="failed",
            error_summary="L2:SCHEMA_MISSING_FIELD@evt_001",
        )
        # t2: quarantine row created from the failed sync
        q_id = await _insert_quarantine(
            session,
            sync_job_id=sync_id,
            created_at=now - timedelta(hours=11),
            entity_id="enr-001",
        )
        # t3: operator retry
        await _insert_retry(
            session,
            sync_job_id=sync_id,
            retried_at=now - timedelta(hours=10),
            reason="rerun after schema fix",
        )
        # t4: operator revert
        await _insert_revert(
            session,
            sync_job_id=sync_id,
            reverted_at=now - timedelta(hours=9),
            snapshots_restored=7,
        )
        # t5: operator resolves the quarantine
        await session.execute(
            text(
                """
                UPDATE quarantine
                SET resolved_at = :ts,
                    resolution_status = 'released',
                    resolution_operator = :op
                WHERE id = :id
                """
            ),
            {
                "id": q_id,
                "ts": now - timedelta(hours=8),
                "op": op_email,
            },
        )
        # t6: scheduled reconciliation
        await _insert_reconciliation(
            session,
            started_at=now - timedelta(hours=4),
            status_value="drift_detected",
            drift_summary=[
                {
                    "entity_type": "enrollments",
                    "canonical_only_ids": ["enr-x"],
                    "partner_only_ids": [],
                    "canonical_mid_hash": "c",
                    "partner_mid_hash": "p",
                }
            ],
        )
        # t7 (newest): operator authorizes the connector
        operator_id = await _insert_operator(
            session,
            subject=op_subject,
            email=op_email,
        )
        await _insert_audit_log(
            session,
            operator_id=operator_id,
            action="connector.authorized",
            created_at=now - timedelta(hours=1),
            reason="POC walk-through",
            detail={"partner": _PARTNER, "created_new_row": True},
        )
        await session.commit()

    try:
        entries = await list_timeline_for_lea(
            db_session_factory, _TEST_LEA, limit=50
        )

        # 7 events total (no separate quarantine_created vs the row's
        # resolved entry is now present, so quarantine contributes 2).
        # Sources expected in newest-first order:
        expected_sources = [
            "audit_log",
            "reconciliation_runs",
            "quarantine_resolved",
            "revert_actions",
            "retry_actions",
            "quarantine_created",
            "sync_jobs",
        ]
        assert [e.source for e in entries] == expected_sources

        by_source = {e.source: e for e in entries}

        # audit_log: actor email resolves via the operator join
        audit = by_source["audit_log"]
        assert audit.actor_kind == "operator"
        assert audit.actor_email == op_email
        assert audit.action == "connector.authorized"
        assert audit.reason == "POC walk-through"
        assert audit.detail is not None
        assert audit.detail["partner"] == _PARTNER

        # sync_jobs: system actor, action prefixed with status
        sync = by_source["sync_jobs"]
        assert sync.actor_kind == "system"
        assert sync.action == "sync.failed"
        assert sync.reason == "L2:SCHEMA_MISSING_FIELD@evt_001"
        assert sync.detail is not None
        assert sync.detail["partner"] == _PARTNER
        assert sync.detail["event_count"] == 1

        # revert / retry: operator actor, text identity flows through
        revert = by_source["revert_actions"]
        assert revert.actor_kind == "operator"
        assert revert.actor_email == "ops@edlink.test"
        assert revert.action == "sync.revert"
        assert revert.detail is not None
        assert revert.detail["snapshots_restored"] == 7

        retry = by_source["retry_actions"]
        assert retry.actor_kind == "operator"
        assert retry.action == "sync.retry_requested"
        assert retry.detail is not None
        assert retry.detail["forced"] is False

        # Quarantine row contributes two timeline entries
        q_created = by_source["quarantine_created"]
        assert q_created.actor_kind == "system"
        assert q_created.action == "quarantine.created"
        assert q_created.target_id == str(q_id)

        q_resolved = by_source["quarantine_resolved"]
        assert q_resolved.actor_kind == "operator"
        assert q_resolved.actor_email == op_email
        assert q_resolved.action == "quarantine.released"
        assert q_resolved.target_id == str(q_id)
        assert q_resolved.id.endswith("#resolved")
        assert q_created.id.endswith("#created")

        # Reconciliation detail carries the drift count
        recon = by_source["reconciliation_runs"]
        assert recon.actor_kind == "system"
        assert recon.action == "reconciliation.drift_detected"
        assert recon.detail is not None
        assert recon.detail["drift_count"] == 1
        assert recon.detail["partner"] == _PARTNER
    finally:
        await _cleanup_operator(db_session_factory, operator_id)


@pytest.mark.asyncio
async def test_timeline_excludes_unresolved_quarantine_resolved_branch(
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    """A quarantine row with NULL resolved_at contributes only the created entry."""

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        sync_id = await _insert_sync(
            session, started_at=now - timedelta(hours=2)
        )
        await _insert_quarantine(
            session,
            sync_job_id=sync_id,
            created_at=now - timedelta(hours=1),
            entity_id="enr-pending",
        )
        await session.commit()

    entries = await list_timeline_for_lea(
        db_session_factory, _TEST_LEA, limit=50
    )
    sources = [e.source for e in entries]
    assert "quarantine_created" in sources
    assert "quarantine_resolved" not in sources


@pytest.mark.asyncio
async def test_timeline_excludes_other_leas(
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    """A sync_job on a different LEA does not appear in this LEA's timeline."""

    other_lea = LeaId("lea-timeline-other")
    now = datetime.now(UTC)

    async with db_session_factory() as session:
        await wipe_lea(session, other_lea)
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'Other', 'traditional_district', 'CA')
                """
            ),
            {"id": other_lea},
        )
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs (
                    id, lea_id, partner, status, started_at, completed_at,
                    event_count, error_count, warning_count,
                    cursor_before, cursor_after, error_summary
                ) VALUES (
                    :id, :lea, 'edlink', 'success', :ts, :ts,
                    0, 0, 0, NULL, NULL, NULL
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "lea": other_lea,
                "ts": now - timedelta(minutes=5),
            },
        )
        # An event on the test LEA so the result is non-empty.
        await _insert_sync(
            session, started_at=now - timedelta(minutes=10)
        )
        await session.commit()

    try:
        entries = await list_timeline_for_lea(
            db_session_factory, _TEST_LEA, limit=50
        )
        assert len(entries) == 1
        assert entries[0].source == "sync_jobs"
    finally:
        async with db_session_factory() as session:
            await wipe_lea(session, other_lea)
            await session.commit()


@pytest.mark.asyncio
async def test_timeline_excludes_audit_rows_with_null_lea(
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    """System-wide audit rows (lea_id IS NULL) stay out of the per-LEA view."""

    now = datetime.now(UTC)
    op_subject = f"timeline-sys-{uuid.uuid4().hex[:8]}"
    async with db_session_factory() as session:
        operator_id = await _insert_operator(
            session,
            subject=op_subject,
            email=f"{op_subject}@edlink.test",
        )
        await session.execute(
            text(
                """
                INSERT INTO audit_log (
                    id, operator_id, action, target_kind, target_id,
                    lea_id, reason, detail, created_at
                ) VALUES (
                    :id, :op, 'operator.role_granted', 'operator',
                    :tid, NULL, 'fixture', NULL, :ts
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "op": operator_id,
                "tid": str(operator_id),
                "ts": now - timedelta(minutes=10),
            },
        )
        await session.commit()

    try:
        entries = await list_timeline_for_lea(
            db_session_factory, _TEST_LEA, limit=50
        )
        assert entries == []
    finally:
        await _cleanup_operator(db_session_factory, operator_id)


# ── HTTP-layer tests ────────────────────────────────────────────────────────


@pytest.fixture
def client() -> TestClient:
    return TestClient(fastapi_app)


async def _seed_auditor(
    factory: async_sessionmaker[Any],
    *,
    subject: str,
) -> tuple[uuid.UUID, dict[str, str]]:
    ensure_test_secret()
    async with factory() as session:
        await session.execute(
            text(
                "DELETE FROM audit_log WHERE operator_id IN"
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
        op_id = await _insert_operator(
            session, subject=subject,
            email=f"{subject}@edlink.test", role="auditor",
        )
        await session.commit()
    return op_id, auth_header(mint_jwt(subject=subject))


def test_timeline_unauthenticated_returns_401(client: TestClient) -> None:
    resp = client.get(f"/api/v1/leas/{_TEST_LEA}/timeline")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_timeline_endpoint_returns_normalized_entries(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    subject = f"timeline-auditor-{uuid.uuid4().hex[:8]}"
    op_id, headers = await _seed_auditor(db_session_factory, subject=subject)

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        await _insert_sync(
            session, started_at=now - timedelta(hours=2),
            status_value="success",
        )
        await _insert_reconciliation(
            session, started_at=now - timedelta(hours=1),
        )
        await session.commit()

    try:
        resp = client.get(
            f"/api/v1/leas/{_TEST_LEA}/timeline", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [e["source"] for e in body] == [
            "reconciliation_runs",
            "sync_jobs",
        ]
        # actor_kind on each
        assert all(e["actor_kind"] == "system" for e in body)
        # detail unpacks as a dict, not a string
        assert isinstance(body[0]["detail"], dict)
    finally:
        await _cleanup_operator(db_session_factory, op_id)


@pytest.mark.asyncio
async def test_timeline_empty_lea_returns_empty_list_not_404(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    subject = f"timeline-empty-{uuid.uuid4().hex[:8]}"
    op_id, headers = await _seed_auditor(db_session_factory, subject=subject)
    try:
        resp = client.get(
            f"/api/v1/leas/{_TEST_LEA}/timeline", headers=headers
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        await _cleanup_operator(db_session_factory, op_id)


@pytest.mark.asyncio
async def test_timeline_unknown_lea_returns_empty_list_not_404(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
) -> None:
    subject = f"timeline-unknown-{uuid.uuid4().hex[:8]}"
    op_id, headers = await _seed_auditor(db_session_factory, subject=subject)
    try:
        resp = client.get(
            "/api/v1/leas/lea-does-not-exist/timeline", headers=headers
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        await _cleanup_operator(db_session_factory, op_id)


@pytest.mark.asyncio
async def test_timeline_limit_caps_response(
    client: TestClient,
    db_session_factory: async_sessionmaker[Any],
    seeded_lea: Any,
) -> None:
    subject = f"timeline-limit-{uuid.uuid4().hex[:8]}"
    op_id, headers = await _seed_auditor(db_session_factory, subject=subject)

    now = datetime.now(UTC)
    async with db_session_factory() as session:
        for i in range(5):
            await _insert_sync(
                session,
                started_at=now - timedelta(hours=i + 1),
            )
        await session.commit()

    try:
        resp = client.get(
            f"/api/v1/leas/{_TEST_LEA}/timeline?limit=2", headers=headers
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2
    finally:
        await _cleanup_operator(db_session_factory, op_id)
