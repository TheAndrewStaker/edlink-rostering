"""ASGI middlewares for the admin API.

Two responsibilities today:

* **Request-ID propagation**: every request gets an ``X-Request-ID``,
  either from the incoming header (correlation across services) or
  freshly minted as a UUID4. The id is bound to a structlog contextvar
  so every log line within the request carries it, and is echoed back
  in the response header so the operator can quote it in a support
  ticket.

* **W3C tracecontext propagation**: if a ``traceparent`` header is
  present, it is bound to the same contextvars so future Azure
  Application Insights (or any OpenTelemetry consumer) can stitch
  spans across services. The header is not generated here; this is
  the consumer seam.

Mounted in :func:`edlink_rostering.api.app.create_app`. Tests against the
FastAPI TestClient go through the same middleware, so a test that
sets ``X-Request-ID: <known-uuid>`` can assert it round-trips through
the response.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


_REQUEST_ID_HEADER = "X-Request-ID"
_TRACEPARENT_HEADER = "traceparent"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind request-scoped context (request_id, traceparent) for logging."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = (
            request.headers.get(_REQUEST_ID_HEADER) or uuid.uuid4().hex
        )
        traceparent = request.headers.get(_TRACEPARENT_HEADER)

        # contextvars are per-task, so clearing before bind is safe and
        # prevents an earlier request's leftover binds from leaking when
        # the event loop reuses a task slot.
        structlog.contextvars.clear_contextvars()
        binds: dict[str, str] = {"request_id": request_id}
        if traceparent:
            binds["traceparent"] = traceparent
        structlog.contextvars.bind_contextvars(**binds)

        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()

        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


__all__ = ["RequestContextMiddleware"]
