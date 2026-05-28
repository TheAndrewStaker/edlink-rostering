"""Idempotency-Key replay tests against the live mutation endpoints.

Three invariants:

1. **Replay**: same key + same body returns the cached response and
   does NOT execute the mutation a second time. Verified using
   ``quarantine.release``: a naive second call would 409
   (QuarantineAlreadyResolved); with the idempotency wrapper, it
   returns the cached 200.
2. **Mismatch**: same key + different body returns 422 with a
   ProblemDetail naming the conflict.
3. **Passthrough**: no header → no caching → standard behavior. The
   second call without a key gets the expected 409 from the service.

Skipped when ``OPS_DATABASE_URL`` / ``DATABASE_URL`` is unset, same
as the rest of the HTTP-level test suite.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
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


_TEST_LEA = LeaId("lea-idem-test")
_OPERATOR_SUBJECT = "idem-test-operator"


@pytest.fixture
def client() -> TestClient:
    return TestClient(fastapi_app)


async def _wipe_idempotency(
    factory: async_sessionmaker[Any], operator_id: uuid.UUID
) -> None:
    async with factory() as session:
        await session.execute(
            text("DELETE FROM idempotency_keys WHERE operator_id = :op"),
            {"op": operator_id},
        )
        await session.commit()


async def _wipe_operator(
    factory: async_sessionmaker[Any], subject: str
) -> None:
    async with factory() as session:
        op_filter = "(SELECT id FROM operator WHERE subject = :s)"
        await session.execute(
            text(f"DELETE FROM audit_log WHERE operator_id IN {op_filter}"),
            {"s": subject},
        )
        await session.execute(
            text(
                f"DELETE FROM idempotency_keys WHERE operator_id IN"
                f" {op_filter}"
            ),
            {"s": subject},
        )
        await session.execute(
            text(
                f"DELETE FROM operator_lea_grant WHERE operator_id IN"
                f" {op_filter}"
            ),
            {"s": subject},
        )
        await session.execute(
            text(
                f"DELETE FROM operator_role WHERE operator_id IN {op_filter}"
            ),
            {"s": subject},
        )
        await session.execute(
            text("DELETE FROM operator WHERE subject = :s"),
            {"s": subject},
        )
        await session.commit()


@pytest_asyncio.fixture
async def seeded_quarantine(
    db_session_factory: async_sessionmaker[Any],
) -> AsyncIterator[dict[str, Any]]:
    """Quarantine row in _TEST_LEA whose pending student also exists.

    The student is present so a release will succeed (no
    QuarantineRefused). Each test resets the quarantine row to
    'pending' between calls to make replay assertions clean.
    """

    ensure_test_secret()
    suffix = uuid.uuid4().hex[:8]
    op_id = uuid.uuid4()
    sync_job_id = uuid.uuid4()
    quarantine_id = uuid.uuid4()
    student_id = f"stu-idem-{suffix}"
    enrollment_id = f"enr-idem-{suffix}"
    now = datetime.now(UTC)

    async with db_session_factory() as session:
        await wipe_lea(session, _TEST_LEA)
        await _wipe_operator(db_session_factory, _OPERATOR_SUBJECT)
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'Idem LEA', 'traditional_district', 'CA')
                """
            ),
            {"id": _TEST_LEA},
        )
        await session.execute(
            text(
                """
                INSERT INTO sync_jobs
                    (id, lea_id, partner, status, started_at, completed_at,
                     event_count)
                VALUES
                    (:id, :lea, 'edlink', 'success', :now, :now, 1)
                """
            ),
            {"id": sync_job_id, "lea": _TEST_LEA, "now": now},
        )
        await session.execute(
            text(
                """
                INSERT INTO students
                    (id, lea_id, given_name, family_name, grade)
                VALUES (:id, :lea, 'Ada', 'L', '8')
                """
            ),
            {"id": student_id, "lea": _TEST_LEA},
        )
        await session.execute(
            text(
                """
                INSERT INTO quarantine
                    (id, sync_job_id, lea_id, entity_type, entity_id,
                     reason, raw_payload, created_at)
                VALUES
                    (:id, :sj, :lea, 'enrollment', :ent, 'orphan',
                     CAST(:payload AS JSONB), :now)
                """
            ),
            {
                "id": quarantine_id,
                "sj": sync_job_id,
                "lea": _TEST_LEA,
                "ent": enrollment_id,
                "payload": (
                    '{"id": "' + enrollment_id + '","lea_id": "'
                    + _TEST_LEA + '","student_id": "' + student_id
                    + '","class_id": "cls-1","begin_date": "2026-08-15"}'
                ),
                "now": now,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO operator (id, subject, display_name, email, status)
                VALUES (:id, :sub, 'Idem Op', 'idem@edlink.test', 'active')
                """
            ),
            {"id": op_id, "sub": _OPERATOR_SUBJECT},
        )
        await session.execute(
            text(
                """
                INSERT INTO operator_role
                    (id, operator_id, role, granted_by, reason)
                VALUES (:id, :op, 'operator', :op, 'test fixture')
                """
            ),
            {"id": uuid.uuid4(), "op": op_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO operator_lea_grant
                    (id, operator_id, lea_id, granted_by, reason)
                VALUES (:id, :op, :lea, :op, 'test fixture')
                """
            ),
            {"id": uuid.uuid4(), "op": op_id, "lea": _TEST_LEA},
        )
        await session.commit()

    token = mint_jwt(subject=_OPERATOR_SUBJECT)

    yield {
        "operator_id": op_id,
        "quarantine_id": quarantine_id,
        "auth": auth_header(token),
    }

    async with db_session_factory() as session:
        await wipe_lea(session, _TEST_LEA)
        await session.commit()
    await _wipe_operator(db_session_factory, _OPERATOR_SUBJECT)


def test_replay_returns_cached_response_no_second_side_effect(
    client: TestClient, seeded_quarantine: dict[str, Any]
) -> None:
    """Second POST with same Idempotency-Key returns cached 200.

    Without the wrapper, a second release on an already-resolved row
    would 409 (QuarantineAlreadyResolved). The fact that the second
    call returns 200 with the same body is the test.
    """

    quarantine_id = seeded_quarantine["quarantine_id"]
    headers = {**seeded_quarantine["auth"], "Idempotency-Key": "release-once"}

    first = client.post(
        f"/api/v1/quarantine/{quarantine_id}/release", headers=headers
    )
    assert first.status_code == 200, first.text
    first_body = first.json()

    second = client.post(
        f"/api/v1/quarantine/{quarantine_id}/release", headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json() == first_body


def test_same_key_different_body_returns_422_problem(
    client: TestClient, seeded_quarantine: dict[str, Any]
) -> None:
    """Same Idempotency-Key reused with a different request body is 422.

    Uses reject (which takes a body) to vary the request. The
    ProblemDetail names the conflict and the status code is the
    advertised 422.
    """

    quarantine_id = seeded_quarantine["quarantine_id"]
    base_headers = {**seeded_quarantine["auth"], "Idempotency-Key": "k-1"}

    first = client.post(
        f"/api/v1/quarantine/{quarantine_id}/reject",
        headers=base_headers,
        json={"reason": "first reason"},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/v1/quarantine/{quarantine_id}/reject",
        headers=base_headers,
        json={"reason": "second reason different body"},
    )
    assert second.status_code == 422, second.text
    payload = second.json()
    assert payload["status"] == 422
    assert "Idempotency-Key" in payload["title"]


def test_missing_key_is_passthrough(
    client: TestClient, seeded_quarantine: dict[str, Any]
) -> None:
    """Without Idempotency-Key, the second release sees the resolved row.

    Standard service-layer 409 (QuarantineAlreadyResolved). Confirms
    the wrapper is genuinely a no-op when the header is absent.
    """

    quarantine_id = seeded_quarantine["quarantine_id"]
    headers = seeded_quarantine["auth"]

    first = client.post(
        f"/api/v1/quarantine/{quarantine_id}/release", headers=headers
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/v1/quarantine/{quarantine_id}/release", headers=headers
    )
    assert second.status_code == 409, second.text
    assert "Already Resolved" in second.json()["title"]


def test_replay_response_shape_matches_first(
    client: TestClient, seeded_quarantine: dict[str, Any]
) -> None:
    """Cached response round-trips through Pydantic without field drift."""

    quarantine_id = seeded_quarantine["quarantine_id"]
    headers = {**seeded_quarantine["auth"], "Idempotency-Key": "shape-check"}

    first = client.post(
        f"/api/v1/quarantine/{quarantine_id}/release", headers=headers
    )
    second = client.post(
        f"/api/v1/quarantine/{quarantine_id}/release", headers=headers
    )
    assert first.json().keys() == second.json().keys()
    assert first.json()["quarantine_id"] == second.json()["quarantine_id"]
    assert (
        first.json()["release_generation_id"]
        == second.json()["release_generation_id"]
    )
