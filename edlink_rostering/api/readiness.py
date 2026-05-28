"""Readiness check registration for the ``/readyz`` probe.

The ``/readyz`` route iterates a registry of dependency probes
instead of hard-coding the Postgres branch with a try/except chain.
When the next dependency joins (Service Bus mock, Key Vault mock,
production HTTP partner ping), the change is one
``register_readiness_check`` call rather than another try/except
branch in :mod:`edlink_rostering.api.app`.

A check is an async callable that returns a :class:`CheckOutcome`.
``ok=True`` means the dependency is healthy; the ``detail`` string is
free-form text the operator reads when triaging a 503. The route
aggregates outcomes into a ``checks`` map keyed by name; the
overall HTTP status is 200 when every check is ok, 503 otherwise.

The registry is process-local. Tests that want to override a check
swap it via :func:`replace_readiness_check`; resetting to the
production set after a test uses :func:`reset_readiness_registry`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text

from edlink_rostering.api.dependencies import get_session_factory


@dataclass(frozen=True)
class CheckOutcome:
    """Result of running one readiness check."""

    name: str
    ok: bool
    detail: str


CheckFn = Callable[[], Awaitable[CheckOutcome]]


_READYZ_PROBE_SQL = text("SELECT 1")


async def _check_postgres() -> CheckOutcome:
    """Confirm a session can be opened and a trivial query runs.

    The probe deliberately keeps the SQL trivial so a transient
    connection-pool blip does not trip readiness for an arbitrary
    expensive query.
    """

    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(_READYZ_PROBE_SQL)
        return CheckOutcome(name="postgres", ok=True, detail="ok")
    except Exception as exc:
        return CheckOutcome(
            name="postgres", ok=False, detail=f"fail: {exc}"
        )


# Per-process registry. Append new checks as dependencies join the
# request path; the route reads from this list, so registration
# order = display order in the response body.
_READINESS_CHECKS: list[CheckFn] = [_check_postgres]


def register_readiness_check(check: CheckFn) -> None:
    """Append a check to the readiness registry.

    Call once at app composition time. The Postgres check ships
    by default; this hook is what Service Bus, Key Vault, or any
    future dependency uses to opt into ``/readyz`` without editing
    the route.
    """

    _READINESS_CHECKS.append(check)


def replace_readiness_check(name: str, check: CheckFn) -> None:
    """Replace a registered check by name. Tests use this to inject mocks."""

    for i, existing in enumerate(_READINESS_CHECKS):
        # Resolve the check's name by running it once would be wrong;
        # checks announce their name via the returned CheckOutcome.
        # So we name the function via __name__ + a convention: the
        # production check's __name__ contains the check name.
        if name in existing.__name__:
            _READINESS_CHECKS[i] = check
            return
    raise KeyError(f"No readiness check matching name {name!r}.")


def registered_checks() -> tuple[CheckFn, ...]:
    """Snapshot of the current readiness check list, in registration order."""

    return tuple(_READINESS_CHECKS)


def reset_readiness_registry() -> None:
    """Restore the production check set. Tests call this in teardown."""

    _READINESS_CHECKS[:] = [_check_postgres]


__all__ = [
    "CheckFn",
    "CheckOutcome",
    "register_readiness_check",
    "registered_checks",
    "replace_readiness_check",
    "reset_readiness_registry",
]
