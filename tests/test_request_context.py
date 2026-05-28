"""RequestContextMiddleware tests.

Two invariants:

1. A request without ``X-Request-ID`` receives a freshly minted one in
   the response, and the structlog contextvar carries it through any
   log lines emitted during the request.
2. A request with an inbound ``X-Request-ID`` (or W3C ``traceparent``)
   round-trips the same value back so multi-service correlation works.

Uses the un-authenticated ``/api/health`` endpoint to keep the test
free of the DB / JWT fixture stack.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from edlink_rostering.api import app as fastapi_app


_UUID_HEX = re.compile(r"^[0-9a-f]{32}$")


def test_health_response_carries_generated_request_id() -> None:
    """No inbound ``X-Request-ID`` means the server mints one (UUID4 hex)."""

    client = TestClient(fastapi_app)
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    request_id = r.headers.get("X-Request-ID")
    assert request_id is not None
    assert _UUID_HEX.match(request_id) is not None


def test_inbound_request_id_is_echoed() -> None:
    """A client-supplied ``X-Request-ID`` round-trips so it can be quoted."""

    client = TestClient(fastapi_app)
    sent = "test-rid-12345"
    r = client.get("/api/health", headers={"X-Request-ID": sent})
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID") == sent


def test_each_request_gets_a_distinct_id() -> None:
    """Generated IDs are per-request, not per-process."""

    client = TestClient(fastapi_app)
    a = client.get("/api/health").headers["X-Request-ID"]
    b = client.get("/api/health").headers["X-Request-ID"]
    assert a != b


def test_healthz_is_unauthenticated_liveness_probe() -> None:
    """``/healthz`` returns 200 with no auth so an orchestrator can probe."""

    client = TestClient(fastapi_app)
    r = client.get("/healthz")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "alive"}


def test_readyz_returns_200_when_db_reachable() -> None:
    """``/readyz`` validates Postgres reachability and returns 200 + checks."""

    import os

    if not (
        os.environ.get("OPS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    ):
        # Without a DB, the probe will fail. That is the correct behavior;
        # the assertion would just be "503 when DB unreachable", which is
        # a different test. Skip in environments without Postgres.
        import pytest

        pytest.skip("OPS_DATABASE_URL/DATABASE_URL not set; skipping readyz probe")

    client = TestClient(fastapi_app)
    r = client.get("/readyz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["postgres"] == "ok"
