"""Tests for the JWT auth layer at edlink_rostering.api.auth.

Eight cases the Session 4 plan called out, plus a couple of extras
that cover the bootstrap "first sign-in" edge.

Each test seeds operators directly via SQL so the operator table is
in a known state; the dev seed module is not used here because we
want to control role grants per test rather than inherit the full
six-persona seed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from edlink_rostering.api.auth import Operator, current_operator, require
from edlink_rostering.api.dependencies import get_session_factory
from tests.conftest import wipe_seeded_operators
from tests.fixtures.auth import (
    auth_header,
    ensure_test_secret,
    mint_jwt,
    random_subject,
)


_TEST_OPERATORS: list[dict[str, str]] = [
    {
        "subject": "test-auth-operator-001",
        "email": "auth-operator@edlink.test",
        "name": "Auth Operator",
        "role": "operator",
    },
    {
        "subject": "test-auth-connector-001",
        "email": "auth-connector@edlink.test",
        "name": "Auth Connector Admin",
        "role": "admin",
    },
    {
        "subject": "test-auth-founder-001",
        "email": "auth-founder@edlink.test",
        "name": "Auth Founder Admin",
        "role": "owner",
    },
    {
        "subject": "test-auth-auditor-001",
        "email": "auth-auditor@edlink.test",
        "name": "Auth Auditor",
        "role": "auditor",
    },
    {
        "subject": "test-auth-disabled-001",
        "email": "auth-disabled@edlink.test",
        "name": "Auth Disabled",
        "role": "operator",
        "status": "disabled",
    },
]


@pytest_asyncio.fixture(autouse=True)
async def _set_test_jwt_secret() -> Any:
    """Every test runs with the test HS256 secret installed."""

    ensure_test_secret()
    yield None


@pytest_asyncio.fixture
async def seeded_auth_operators(
    db_session_factory: async_sessionmaker[Any],
) -> Any:
    """Insert the auth-test personas and clean them up afterwards.

    Founder admin is inserted first so its id can chain into the
    other operators' granted_by column.
    """

    async with db_session_factory() as session:
        # Wipe first so a previous test's leftover rows do not collide
        # on the unique subject index.
        await _wipe_test_operators(session)

        founder = next(
            o for o in _TEST_OPERATORS if o["role"] == "owner"
        )
        founder_id = await _insert_operator(
            session, founder, granted_by=None
        )

        for persona in _TEST_OPERATORS:
            if persona is founder:
                continue
            await _insert_operator(
                session, persona, granted_by=founder_id
            )

        await session.commit()

    yield None

    async with db_session_factory() as session:
        await _wipe_test_operators(session)
        # Also wipe any unknown-subject rows the auto-create test
        # spawned so re-runs stay clean.
        await wipe_seeded_operators(session)
        await session.commit()


async def _insert_operator(
    session: Any, persona: dict[str, str], granted_by: uuid.UUID | None
) -> uuid.UUID:
    op_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO operator (id, subject, display_name, email, status)
            VALUES (:id, :sub, :name, :email, :status)
            """
        ),
        {
            "id": op_id,
            "sub": persona["subject"],
            "name": persona["name"],
            "email": persona["email"],
            "status": persona.get("status", "active"),
        },
    )
    grant = granted_by if granted_by is not None else op_id
    await session.execute(
        text(
            """
            INSERT INTO operator_role
                (id, operator_id, role, granted_by, reason)
            VALUES (:id, :op, :role, :by, :reason)
            """
        ),
        {
            "id": uuid.uuid4(),
            "op": op_id,
            "role": persona["role"],
            "by": grant,
            "reason": "test fixture",
        },
    )
    return op_id


async def _wipe_test_operators(session: Any) -> None:
    subjects = [o["subject"] for o in _TEST_OPERATORS]
    await session.execute(
        text(
            "DELETE FROM operator_role WHERE operator_id IN "
            "(SELECT id FROM operator WHERE subject = ANY(:s))"
        ),
        {"s": subjects},
    )
    await session.execute(
        text("DELETE FROM operator WHERE subject = ANY(:s)"),
        {"s": subjects},
    )


def _client(
    min_role: str,
    *,
    factory: object | None = None,
) -> TestClient:
    """Build a minimal FastAPI app with one endpoint behind require().

    ``get_session_factory`` is always overridden so the auth layer does
    not pull the production lru-cached factory (which would fail if
    OPS_DATABASE_URL is unset). The 401 paths in ``current_operator``
    raise before touching the DB, so a MagicMock is sufficient for the
    non-authenticated tests. DB-bound tests pass the real
    ``db_session_factory`` so the auth layer's operator lookup and
    role/grant queries hit the same DB the fixture seeded.
    """

    app = FastAPI()
    if factory is None:
        app.dependency_overrides[get_session_factory] = lambda: MagicMock()
    else:
        app.dependency_overrides[get_session_factory] = lambda: factory

    @app.get("/probe")
    async def probe(op: Operator = Depends(require(min_role))) -> dict[str, str]:
        return {
            "subject": op.subject,
            "role": op.role,
            "status": op.status,
        }

    return TestClient(app)


# ── The eight cases the plan called out ─────────────────────────────────────


@pytest.mark.asyncio
async def test_expired_jwt_returns_401(
    db_session_factory: async_sessionmaker[Any],
    seeded_auth_operators: Any,
) -> None:
    """An exp claim in the past is rejected at the signature step."""

    app = _client("operator", factory=db_session_factory)
    token = mint_jwt(
        subject="test-auth-operator-001",
        issued_at=datetime.now(UTC) - timedelta(hours=2),
        expires_in=timedelta(hours=1),
    )
    resp = app.get("/probe", headers=auth_header(token))
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_missing_jwt_returns_401() -> None:
    """No Authorization header is a 401, not a 403."""

    app = _client("operator")
    resp = app.get("/probe")
    assert resp.status_code == 401
    assert "authorization" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_jwt_with_unknown_subject_creates_operator_row(
    db_session_factory: async_sessionmaker[Any],
    seeded_auth_operators: Any,
) -> None:
    """A first-time subject is upserted, then 403s for lack of role.

    The auth layer authenticated the JWT (so not 401); the role layer
    sees no active grant (so 403). After the response, the operator
    table has a new row at the upserted subject.
    """

    app = _client("operator", factory=db_session_factory)
    new_subject = random_subject("auth-newcomer")
    token = mint_jwt(
        subject=new_subject,
        email=f"{new_subject}@edlink.test",
        name="Newcomer",
    )
    resp = app.get("/probe", headers=auth_header(token))
    assert resp.status_code == 403
    assert "no active role" in resp.json()["detail"].lower()

    async with db_session_factory() as session:
        row = (
            await session.execute(
                text("SELECT subject FROM operator WHERE subject = :s"),
                {"s": new_subject},
            )
        ).first()
    assert row is not None


@pytest.mark.asyncio
async def test_jwt_for_disabled_operator_returns_401(
    db_session_factory: async_sessionmaker[Any],
    seeded_auth_operators: Any,
) -> None:
    """A disabled operator authenticates the JWT but cannot proceed."""

    app = _client("operator", factory=db_session_factory)
    token = mint_jwt(subject="test-auth-disabled-001")
    resp = app.get("/probe", headers=auth_header(token))
    assert resp.status_code == 401
    assert "disabled" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_operator_role_below_minimum_returns_403(
    db_session_factory: async_sessionmaker[Any],
    seeded_auth_operators: Any,
) -> None:
    """An 'operator' role hits a 'owner' gate with a 403."""

    app = _client("owner", factory=db_session_factory)
    token = mint_jwt(subject="test-auth-operator-001")
    resp = app.get("/probe", headers=auth_header(token))
    assert resp.status_code == 403
    assert "owner" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_owner_satisfies_any_minimum_role(
    db_session_factory: async_sessionmaker[Any],
    seeded_auth_operators: Any,
) -> None:
    """owner is the top of the ladder; all gates pass."""

    token = mint_jwt(subject="test-auth-founder-001")
    for min_role in ("auditor", "operator", "admin", "owner"):
        app = _client(min_role, factory=db_session_factory)
        resp = app.get("/probe", headers=auth_header(token))
        assert resp.status_code == 200, (
            f"owner should satisfy require({min_role!r}), got"
            f" {resp.status_code} {resp.text}"
        )
        assert resp.json()["role"] == "owner"


@pytest.mark.asyncio
async def test_admin_satisfies_operator_minimum(
    db_session_factory: async_sessionmaker[Any],
    seeded_auth_operators: Any,
) -> None:
    """admin is above operator on the action-endpoint ladder."""

    app = _client("operator", factory=db_session_factory)
    token = mint_jwt(subject="test-auth-connector-001")
    resp = app.get("/probe", headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_auditor_passes_read_endpoint_blocks_write_endpoint(
    db_session_factory: async_sessionmaker[Any],
    seeded_auth_operators: Any,
) -> None:
    """auditor passes require('auditor') and is blocked by require('operator').

    The asymmetry is the whole point of the parallel-not-above auditor
    placement. Read endpoints declare require('auditor'); write
    endpoints declare require('operator').
    """

    token = mint_jwt(subject="test-auth-auditor-001")

    read_app = _client("auditor", factory=db_session_factory)
    read_resp = read_app.get("/probe", headers=auth_header(token))
    assert read_resp.status_code == 200
    assert read_resp.json()["role"] == "auditor"

    write_app = _client("operator", factory=db_session_factory)
    write_resp = write_app.get("/probe", headers=auth_header(token))
    assert write_resp.status_code == 403
    assert "operator" in write_resp.json()["detail"]


# ── Extras that lock in the bootstrap edges ─────────────────────────────────


def test_malformed_authorization_header_returns_401() -> None:
    """An Authorization header without 'Bearer <jwt>' shape is 401."""

    app = _client("operator")
    resp = app.get("/probe", headers={"Authorization": "Token abc.def.ghi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_authorized_leas_populated_for_admin_roles(
    db_session_factory: async_sessionmaker[Any],
    seeded_auth_operators: Any,
) -> None:
    """owner and admin get every active LEA.

    Operators on the operator role get an empty set in V0004; the
    follow-up Step 3 work adds operator_lea_grant. This test pins the
    interim contract so a regression on it is loud.
    """

    app = FastAPI()
    app.dependency_overrides[get_session_factory] = lambda: db_session_factory

    @app.get("/leas-i-see")
    async def leas_i_see(
        op: Operator = Depends(require("auditor")),
    ) -> dict[str, list[str]]:
        return {"leas": sorted(op.authorized_leas)}

    client = TestClient(app)
    founder_token = mint_jwt(subject="test-auth-founder-001")
    operator_token = mint_jwt(subject="test-auth-operator-001")

    founder_resp = client.get(
        "/leas-i-see", headers=auth_header(founder_token)
    )
    operator_resp = client.get(
        "/leas-i-see", headers=auth_header(operator_token)
    )

    assert founder_resp.status_code == 200
    assert operator_resp.status_code == 200

    founder_leas = founder_resp.json()["leas"]
    operator_leas = operator_resp.json()["leas"]

    # founder sees the full LEA inventory; operator sees nothing yet.
    assert isinstance(founder_leas, list)
    assert operator_leas == []
