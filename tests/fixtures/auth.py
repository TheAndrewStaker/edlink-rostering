"""Test helpers for the JWT auth layer.

Tests mint JWTs against the same HS256 secret the auth module reads
from ``DEV_JWT_SECRET``. The minter accepts arbitrary subjects so a
test can simulate any operator persona (or a brand-new subject the
operator table has not seen yet).

The dev React app's persona switcher (Phase 1.5b Step 9) will use the
same secret-backed minter through a dev-only ``/api/dev/mint-jwt``
endpoint; this fixture is the test-suite analogue.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt

_DEFAULT_TEST_SECRET = "test-jwt-secret-not-for-prod-do-not-use-do-not-paste"


def ensure_test_secret() -> str:
    """Set DEV_JWT_SECRET in the process env if it is not already.

    Called by ``mint_jwt`` so a test that forgets the explicit
    fixture still mints valid tokens. Returns the active secret so
    callers can pass it through if they want to mint outside the
    helper.

    Clears the :func:`edlink_rostering.core.settings.get_settings` LRU cache
    on first set so the auth module picks up the late env mutation
    rather than the stale cached Settings instance.
    """

    secret = os.environ.get("DEV_JWT_SECRET")
    if secret:
        return secret
    os.environ["DEV_JWT_SECRET"] = _DEFAULT_TEST_SECRET
    from edlink_rostering.core.settings import get_settings

    get_settings.cache_clear()
    return _DEFAULT_TEST_SECRET


def mint_jwt(
    *,
    subject: str,
    email: str | None = None,
    name: str | None = None,
    expires_in: timedelta = timedelta(minutes=15),
    issued_at: datetime | None = None,
    extra_claims: dict[str, object] | None = None,
) -> str:
    """Mint a signed HS256 JWT with the standard JWT claims.

    The minimal claim set the auth module reads is ``sub``, ``exp``,
    optional ``email`` and ``name``. Other claims pass through via
    ``extra_claims`` so a test can simulate clock-skewed or otherwise
    weird tokens.
    """

    secret = ensure_test_secret()
    now = issued_at or datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
        "nbf": int(now.timestamp()),
    }
    if email is not None:
        claims["email"] = email
    if name is not None:
        claims["name"] = name
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, secret, algorithm="HS256")


def auth_header(token: str) -> dict[str, str]:
    """Convenience wrapper so tests read fluently."""

    return {"Authorization": f"Bearer {token}"}


def random_subject(prefix: str = "test") -> str:
    """A unique subject suitable for the first-sign-in test path."""

    return f"{prefix}-{uuid.uuid4().hex[:12]}"


__all__ = [
    "auth_header",
    "ensure_test_secret",
    "mint_jwt",
    "random_subject",
]
