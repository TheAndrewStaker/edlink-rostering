"""Readiness check registry tests.

Pins the registered-checks pattern from
:mod:`edlink_rostering.api.readiness`. The existing
``test_readyz_returns_200_when_db_reachable`` in
``test_request_context.py`` already covers the happy Postgres path,
so this file targets the new affordance: a third-party check joins
the probe via :func:`register_readiness_check`, and a failing check
turns the 200 into a 503 carrying every check's detail in the body.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from edlink_rostering.api import app as fastapi_app
from edlink_rostering.api.readiness import (
    CheckOutcome,
    register_readiness_check,
    registered_checks,
    reset_readiness_registry,
)


pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("OPS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    ),
    reason="OPS_DATABASE_URL/DATABASE_URL not set; readyz needs Postgres",
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(fastapi_app)


@pytest.fixture(autouse=True)
def restore_registry() -> object:
    """Every test gets a clean registry back."""

    yield
    reset_readiness_registry()


def test_openapi_schema_generation_does_not_500(client: TestClient) -> None:
    """Regression pin: /openapi.json must succeed.

    The first cut of /readyz used a string-quoted ``-> "JSONResponse"``
    return annotation, which Pydantic's schema generator could not
    resolve. That crashed openapi schema generation and bricked
    Swagger UI plus any tool that introspects the spec. The fix
    pairs ``response_model=None`` with a real (non-string) annotation;
    this test pins it.
    """

    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text


def test_registry_ships_with_postgres_check() -> None:
    """Postgres is registered by default so production starts ready."""

    names = [
        check.__name__ for check in registered_checks()
    ]
    assert any("postgres" in name for name in names)


def test_extra_check_joins_the_probe(client: TestClient) -> None:
    """A registered third-party check shows up in the 200 response body."""

    async def _check_service_bus() -> CheckOutcome:
        return CheckOutcome(name="service_bus", ok=True, detail="ok")

    register_readiness_check(_check_service_bus)
    r = client.get("/readyz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["service_bus"] == "ok"


def test_failing_check_flips_response_to_503(client: TestClient) -> None:
    """One failing check is enough to mark the replica unready.

    Every check still runs, so the 503 body carries the postgres
    detail too. That lets the operator distinguish "DB is fine, the
    new dependency is wedged" from "DB went away."
    """

    async def _check_failing_dependency() -> CheckOutcome:
        return CheckOutcome(
            name="failing_dependency",
            ok=False,
            detail="fail: synthetic outage",
        )

    register_readiness_check(_check_failing_dependency)
    r = client.get("/readyz")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "unready"
    checks = body["checks"]
    assert checks["postgres"] == "ok"
    assert checks["failing_dependency"].startswith("fail:")
