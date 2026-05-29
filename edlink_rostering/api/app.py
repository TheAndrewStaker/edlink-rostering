"""FastAPI app for the admin surface.

Thin composition layer: builds the app, attaches CORS for the Vite dev
server, registers the RFC 7807 exception handler family, and mounts
each route family. Routes live in ``edlink_rostering/api/routers/`` so this
file stays under the file-scope budget and adding a new family is a
one-line change.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from edlink_rostering.api.errors import register_error_handlers, register_problem
from edlink_rostering.api.middleware import RequestContextMiddleware
from edlink_rostering.api.readiness import registered_checks
from edlink_rostering.api.routers import (
    actions,
    alerts,
    audit,
    connectors,
    cursors,
    dev,
    dev_test_events,
    leas,
    quarantine,
    reconciliation,
    syncs,
    timeline,
)
from edlink_rostering.core.logging import configure_logging
from edlink_rostering.core.settings import get_settings
from edlink_rostering.services.connector_authz import (
    ConnectorAuthorizationNotFound,
)
from edlink_rostering.services.idempotency import (
    IdempotencyConflict,
    IdempotencyInFlight,
)
from edlink_rostering.services.quarantine import (
    QuarantineAlreadyResolved,
    QuarantineNotFound,
    QuarantineRefused,
)
from edlink_rostering.services.retry import RetryRefused, RetrySyncJobNotFound
from edlink_rostering.services.revert import RevertRefused, RevertSyncJobNotFound
from edlink_rostering.services.test_events import ScenarioNotFound


# Domain exception → ProblemDetail mapping. One source of truth: every
# router used to repeat a try/except wall that did the same conversion;
# now they just `raise` and the global handler does the rest.
register_problem(
    RetrySyncJobNotFound, status=404, title="Sync Job Not Found"
)
register_problem(RetryRefused, status=409, title="Retry Refused")
register_problem(
    RevertSyncJobNotFound, status=404, title="Sync Job Not Found"
)
register_problem(RevertRefused, status=409, title="Revert Refused")
register_problem(
    QuarantineNotFound, status=404, title="Quarantine Row Not Found"
)
register_problem(
    QuarantineAlreadyResolved,
    status=409,
    title="Quarantine Already Resolved",
)
register_problem(QuarantineRefused, status=409, title="Quarantine Refused")
register_problem(
    ConnectorAuthorizationNotFound,
    status=404,
    title="Connector Authorization Not Found",
)
register_problem(ScenarioNotFound, status=404, title="Test Scenario Not Found")
register_problem(
    IdempotencyConflict,
    status=422,
    title="Idempotency-Key Body Mismatch",
)
register_problem(
    IdempotencyInFlight,
    status=409,
    title="Idempotency-Key In Flight",
)


_startup_logger: structlog.stdlib.BoundLogger = structlog.get_logger("edlink_rostering.startup")


def _git_describe(prototype_dir: Path) -> tuple[str, str]:
    """Return (commit, branch) for the running process.

    Best-effort: when git is unavailable or the working tree is
    detached, returns ``("unknown", "unknown")``. The point is
    operator visibility ("the API I'm hitting is from this commit"),
    not strict provenance, so a missing value is fine.
    """

    def _run(args: list[str]) -> str:
        # stdin=DEVNULL is required on Windows when this code runs
        # under pytest, where the inherited stdin handle is not
        # duplicable and DuplicateHandle raises WinError 50. Capture
        # output goes to pipes so it cannot inherit either; both
        # streams are explicitly redirected.
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=prototype_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
                check=False,
            )
            return result.stdout.strip() or "unknown"
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return "unknown"

    return _run(["rev-parse", "--short", "HEAD"]), _run(
        ["rev-parse", "--abbrev-ref", "HEAD"]
    )


def _log_startup_banner(port_api: int) -> None:
    """Emit one structured log line identifying the running process.

    The "I forgot the API was running stale code" footgun gets noisier
    when nothing announces which commit is on the wire. This banner
    fires once at create_app() time and prints commit + branch + port
    so a quick uvicorn log scroll answers "which build is this?"
    """

    prototype_dir = Path(__file__).resolve().parents[2]
    commit, branch = _git_describe(prototype_dir)
    _startup_logger.info(
        "api.startup",
        commit=commit,
        branch=branch,
        port=port_api,
    )


def create_app() -> FastAPI:
    """Construct the FastAPI app.

    Exposed as a factory so tests can build fresh instances per test
    when they need to. The module-level :data:`app` is the default
    instance uvicorn loads.
    """

    settings = get_settings()
    configure_logging(profile=settings.EDLINK_PROFILE)

    _log_startup_banner(settings.port_api)

    app = FastAPI(
        title="EdLink rostering framework admin API",
        version="0.1.0",
        description=(
            "Read + action surface backing the Phase 1 Chakra admin app. "
            "Wraps the same service classes the operator CLI uses."
        ),
    )

    # Request-ID middleware runs first so structlog contextvars are
    # bound before any route or exception handler emits a log line.
    app.add_middleware(RequestContextMiddleware)

    # The Vite dev server runs on http://localhost:${PORT_WEB} per
    # _lib.sh's derivation. CORS in production is restricted to the
    # admin origin; the dev-only allow-list reads the port
    # from settings so a EDLINK_PORT_BASE change in .env propagates
    # without editing this file. ``X-Request-ID`` is exposed so the
    # browser DevTools network panel surfaces it for support-ticket
    # correlation. ``allow_methods`` enumerates every verb the API may
    # use today or tomorrow so a new PATCH/PUT/DELETE route does not
    # need a CORS tweak alongside the route addition.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://localhost:{settings.port_web}",
            f"http://127.0.0.1:{settings.port_web}",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
    )

    register_error_handlers(app)

    # All routers mount under /api/v1. Routers declare just their
    # aggregate sub-path (e.g., /leas, /quarantine, /admin/audit);
    # the version prefix is centralized here so a future /api/v2
    # is one extra include_router call rather than a 12-router
    # search-and-replace.
    api_v1_prefix = "/api/v1"
    app.include_router(leas.router, prefix=api_v1_prefix)
    app.include_router(syncs.router, prefix=api_v1_prefix)
    app.include_router(actions.router, prefix=api_v1_prefix)
    app.include_router(quarantine.router, prefix=api_v1_prefix)
    app.include_router(cursors.router, prefix=api_v1_prefix)
    app.include_router(alerts.router, prefix=api_v1_prefix)
    app.include_router(connectors.router, prefix=api_v1_prefix)
    app.include_router(reconciliation.router, prefix=api_v1_prefix)
    app.include_router(timeline.router, prefix=api_v1_prefix)
    app.include_router(audit.router, prefix=api_v1_prefix)
    if dev.is_dev_profile():
        # Dev profile mounts the persona switcher's JWT minter and the
        # "Send test event" dispatcher. Each router checks the env on
        # every call too, so a mid-process profile flip cannot leak
        # the endpoints.
        app.include_router(dev.router, prefix=api_v1_prefix)
        app.include_router(dev_test_events.router, prefix=api_v1_prefix)

    @app.get("/api/health", operation_id="health.check", tags=["health"])
    async def health() -> dict[str, str]:
        """Legacy health endpoint kept for the admin UI footer."""

        return {"status": "ok"}

    @app.get("/healthz", operation_id="health.liveness", tags=["health"])
    async def healthz() -> dict[str, str]:
        """Kubernetes-style liveness probe.

        Returns 200 as long as the process is up. Does not validate
        downstream dependencies because liveness is only "is this
        container still useful" and the orchestrator should not
        recycle a pod just because the DB hiccupped.
        """

        return {"status": "alive"}

    @app.get(
        "/readyz",
        operation_id="health.readiness",
        tags=["health"],
        # response_model=None tells FastAPI not to inspect the return
        # annotation for OpenAPI schema generation; the route returns
        # a Response directly. Without this, Pydantic tries to build
        # a TypeAdapter for ``JSONResponse`` and the schema generator
        # crashes (PydanticUserError: class-not-fully-defined), which
        # breaks /openapi.json and the Swagger UI.
        response_model=None,
    )
    async def readyz() -> JSONResponse:
        """Kubernetes-style readiness probe.

        Iterates the registry from :mod:`edlink_rostering.api.readiness` so a
        new dependency (Service Bus mock, Key Vault mock, partner HTTP
        ping) joins the probe via one ``register_readiness_check`` call
        rather than a new try/except branch in this route. Returns 200
        with a ``checks`` map when every registered check is ok; 503
        with the same map when at least one fails so the orchestrator
        stops routing traffic to this replica.

        Returns ``JSONResponse`` directly so the failure path can carry
        the full per-check map. The RFC 7807 handler in
        :mod:`edlink_rostering.api.errors` only preserves a single ``detail``
        string from ``HTTPException`` payloads; that's the right shape
        for application errors and the wrong shape for an orchestrator
        probe whose value is the per-dependency breakdown.
        """

        checks: dict[str, str] = {}
        any_failed = False
        for check in registered_checks():
            outcome = await check()
            checks[outcome.name] = outcome.detail
            if not outcome.ok:
                any_failed = True

        if any_failed:
            return JSONResponse(
                status_code=503,
                content={"status": "unready", "checks": checks},
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "checks": checks},
        )

    return app


app = create_app()


__all__ = ["app", "create_app"]
