"""Tests for the RFC 7807 error handlers.

Focused on the catch-all ``Exception`` handler added so unhandled
failures land on a durable telemetry sink and return a structured 500
instead of a bare ``Internal Server Error`` string. The domain-exception
and HTTPException mappings are exercised indirectly by the router tests;
this file pins the last-resort behaviour that those don't cover.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from edlink_rostering.api import dependencies
from edlink_rostering.api.errors import register_error_handlers
from edlink_rostering.infrastructure.azure_mocks.app_insights import (
    MemorySink,
    Telemetry,
)


def test_unhandled_exception_returns_problem_json_and_records_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmapped exception → 500 problem+json, traceback to telemetry.

    The client sees a generic detail (no internal leak per
    security.md); the full exception is captured on the telemetry sink
    so the 500 is self-diagnosing rather than invisible.
    """

    sink = MemorySink()
    telemetry = Telemetry(sinks=[sink])
    # The handler does `from ...dependencies import get_telemetry` at call
    # time, so patching the attribute on the module is picked up.
    monkeypatch.setattr(dependencies, "get_telemetry", lambda: telemetry)

    app = FastAPI()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    register_error_handlers(app)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == 500
    assert body["title"] == "Internal Server Error"
    assert body["instance"] == "/boom"
    assert body["detail"] == "An unexpected error occurred."
    # The internal message never reaches the client.
    assert "internal detail that must not leak" not in resp.text

    # ...but it is captured durably in telemetry.
    exc_records = [r for r in sink.records if r.kind == "exception"]
    assert len(exc_records) == 1
    props = exc_records[0].properties
    assert props["exception_type"] == "RuntimeError"
    assert props["path"] == "/boom"
    assert props["method"] == "GET"
