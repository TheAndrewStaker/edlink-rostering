"""RFC 7807 Problem Details for the admin API.

Centralizes the service-exception to HTTP-status mapping that used to
live as repeated try/except walls inside every router. Each domain
exception registers once via :func:`register_problem`; the global
handler converts it (and any plain ``HTTPException``) to a
``ProblemDetail`` JSON response with ``Content-Type:
application/problem+json``. A catch-all ``Exception`` handler records
anything unmapped to the telemetry sink and returns a structured 500,
so unhandled failures are never invisible.

RFC 7807 field semantics:

* ``type``      URI identifying the problem class. Defaults to
                ``about:blank`` when no specific class URI is registered.
* ``title``     Short human-readable summary of the problem class.
                Stable across occurrences.
* ``status``    Numeric HTTP status code.
* ``detail``    Human-readable explanation specific to this occurrence.
                Pulled from ``str(exc)`` for domain exceptions and from
                ``HTTPException.detail`` for default FastAPI errors.
* ``instance``  URI identifying this specific occurrence; we use the
                request path so a 404 on ``/api/leas/foo`` is
                distinguishable from a 404 on ``/api/syncs/{id}``.

Spec: https://www.rfc-editor.org/rfc/rfc7807
"""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


_PROBLEM_JSON = "application/problem+json"


class ProblemDetail(BaseModel):
    """RFC 7807 problem details payload."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


# Status-code → human title for the default HTTPException handler. Service
# exceptions register their own titles via :func:`register_problem`.
_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    410: "Gone",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


_DOMAIN_EXCEPTIONS: dict[type[Exception], tuple[int, str]] = {}


def register_problem(
    exc_type: type[Exception], *, status: int, title: str
) -> None:
    """Register a domain exception → (status, title) mapping.

    Idempotent: a re-registration with the same status+title is a no-op
    (so tests that build fresh apps via :func:`create_app` are safe);
    a re-registration with different values raises ``RuntimeError``
    because that is almost certainly a typo, not an intent to override.

    Call this at import time of ``edlink_rostering.api.app`` (or at module import
    of the service that owns the exception). The actual handler wiring
    happens in :func:`register_error_handlers`.
    """

    existing = _DOMAIN_EXCEPTIONS.get(exc_type)
    if existing is not None and existing != (status, title):
        raise RuntimeError(
            f"register_problem({exc_type.__name__}) called twice with"
            f" different values: {existing!r} vs ({status!r}, {title!r})."
        )
    _DOMAIN_EXCEPTIONS[exc_type] = (status, title)


def _problem_response(
    *, status_code: int, title: str, detail: str | None, instance: str | None
) -> JSONResponse:
    body = ProblemDetail(
        title=title,
        status=status_code,
        detail=detail,
        instance=instance,
    ).model_dump()
    return JSONResponse(
        status_code=status_code, content=body, media_type=_PROBLEM_JSON
    )


def _build_domain_handler(
    status_code: int, title: str
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return _problem_response(
            status_code=status_code,
            title=title,
            detail=str(exc) or None,
            instance=str(request.url.path),
        )

    return handler


async def _http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Convert any ``HTTPException`` (manual or framework) to ProblemDetail.

    Preserves the existing ``detail`` field so consumers that already
    check ``response.json()["detail"]`` keep working; the ProblemDetail
    payload is a superset, not a rename.
    """

    raw = exc.detail
    if isinstance(raw, str):
        detail_text: str | None = raw
        title = _STATUS_TITLES.get(exc.status_code, f"HTTP {exc.status_code}")
    elif isinstance(raw, dict):
        detail_text = (
            str(raw.get("detail"))
            if raw.get("detail") is not None
            else None
        )
        title = (
            str(raw.get("title"))
            if raw.get("title") is not None
            else _STATUS_TITLES.get(exc.status_code, f"HTTP {exc.status_code}")
        )
    else:
        detail_text = str(raw) if raw is not None else None
        title = _STATUS_TITLES.get(exc.status_code, f"HTTP {exc.status_code}")

    return _problem_response(
        status_code=exc.status_code,
        title=title,
        detail=detail_text,
        instance=str(request.url.path),
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Last-resort handler for exceptions with no registered mapping.

    Without this, an unhandled exception produces a bare
    ``Internal Server Error`` string and the traceback goes only to the
    server's stdout, never to a durable sink, so a 500 in a long-running
    dev server is invisible after the terminal scrolls. This records the
    exception via the telemetry facade (in dev: stdout plus the
    ``var/logs/app_insights.jsonl`` FileSink) so the class is
    self-diagnosing, then returns an RFC 7807 500.

    The response ``detail`` is deliberately generic: per
    ``.claude/rules/security.md`` internal error text and tracebacks
    never reach the client. The full exception lives in telemetry.
    """

    # Lazy import: ``dependencies`` pulls in infrastructure wiring, and
    # this module is imported at app-construction time. Importing inside
    # the handler keeps the module-load graph acyclic.
    from edlink_rostering.api.dependencies import get_telemetry

    try:
        get_telemetry().track_exception(
            exc,
            properties={
                "path": str(request.url.path),
                "method": request.method,
            },
        )
    except Exception:
        # Telemetry must never mask or replace the original failure.
        pass

    return _problem_response(
        status_code=500,
        title=_STATUS_TITLES[500],
        detail="An unexpected error occurred.",
        instance=str(request.url.path),
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Convert FastAPI's 422 RequestValidationError to ProblemDetail.

    The raw ``errors()`` list is preserved as ``detail`` (compact JSON
    string) so clients can still parse the field-level errors. Title is
    a stable string for the 422 class.
    """

    return _problem_response(
        status_code=422,
        title=_STATUS_TITLES[422],
        detail=str(exc.errors()),
        instance=str(request.url.path),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Wire all registered domain exceptions and the framework handlers.

    Call once from :func:`edlink_rostering.api.app.create_app` after all routers
    are mounted. Order does not matter because FastAPI dispatches on
    exception class, not registration order.
    """

    app.add_exception_handler(HTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError, _validation_exception_handler  # type: ignore[arg-type]
    )
    # Catch-all so unmapped exceptions land on a durable telemetry sink
    # and return a structured 500 instead of a bare string. Registered
    # before the domain handlers, but Starlette dispatches on the most
    # specific exception class, so mapped domain exceptions still win.
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    for exc_type, (status_code, title) in _DOMAIN_EXCEPTIONS.items():
        app.add_exception_handler(
            exc_type, _build_domain_handler(status_code, title)
        )


__all__ = [
    "ProblemDetail",
    "register_error_handlers",
    "register_problem",
]
