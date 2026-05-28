"""In-memory mock of Azure Key Vault for per-LEA bearer tokens.

Mirrors the subset of ``azure.keyvault.secrets.SecretClient`` that the EdLink
connector uses: ``get_secret(name)`` returning an object with a ``.value``
attribute.

Secret values come from one of two sources:

1. Environment variables prefixed with ``KEYVAULT__`` (double underscore).
   ``KEYVAULT__EDLINK_TOKEN_LEA_123`` is exposed as secret name
   ``edlink-token-lea-123`` (case-insensitive on the prefix, lowercased and
   hyphenated for the name).
2. Direct ``put_secret`` calls from test setup.

This keeps real secrets out of fixture files and lets tests stage values
without touching the environment.

Read caching: ``get_secret`` memoizes results with a 60-second TTL. Real
Azure Key Vault enforces a per-vault read rate limit (about 2000 transactions
per 10 seconds). At fleet scale (100+ LEAs polling at 1-min cadence) a
naive per-poll read against the live vault would saturate that limit. The
``docs/design/edlink-oneroster-rostering.md`` Mocked surfaces section
predicts this bottleneck; the cache lands before the swap to a real
``SecretClient`` rather than after the page. ``put_secret`` invalidates the
cached entry so tests staging new values see them immediately.

Transient-failure injection (``inject_transient_failures(n)``): the next
``n`` calls to :meth:`get_secret_with_retry` raise an
``httpx.ConnectError``. The wrapper uses
:func:`edlink_rostering.core.retry.with_retry`, so the policy classifies the
error as retryable and replays. This makes the tenacity policy
load-bearing today on the mock surface, not just on the production HTTP
client that lands later. Test coverage lives in
``tests/test_azure_mocks_retry.py``.
"""

from __future__ import annotations

import os
import time

import httpx
from tenacity.stop import stop_base
from tenacity.wait import wait_base

from edlink_rostering.core.retry import RETRY_STOP, RETRY_WAIT, with_retry
from edlink_rostering.infrastructure.ports import (
    SecretNotFound,
    SecretValue,
)

KeyVaultSecret = SecretValue


class KeyVaultClient:
    """Process-local secret store with a 60-second read cache.

    One instance per application; the EdLink connector takes it as a
    constructor argument to avoid a module-level singleton.
    """

    _ENV_PREFIX = "KEYVAULT__"
    _CACHE_TTL_SECONDS = 60.0

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._cache: dict[str, tuple[KeyVaultSecret, float]] = {}
        self._transient_failures_remaining: int = 0
        self._load_from_env()

    def inject_transient_failures(self, count: int) -> None:
        """Make the next ``count`` retry-wrapped calls raise ``httpx.ConnectError``.

        The ``with_retry`` wrapper classifies ``httpx.ConnectError`` as
        retryable, so this method is the seam for testing that the
        production policy actually replays after a synthetic Key Vault
        outage. Affects :meth:`get_secret_with_retry` only; the plain
        :meth:`get_secret` path is unaffected so existing tests stay
        deterministic.
        """

        self._transient_failures_remaining = max(0, count)

    def _load_from_env(self) -> None:
        for key, value in os.environ.items():
            if not key.startswith(self._ENV_PREFIX):
                continue
            name = key[len(self._ENV_PREFIX) :].lower().replace("_", "-")
            self._store[name] = value

    def put_secret(self, name: str, value: str) -> None:
        """Stage a secret. Used by tests and bootstrap scripts.

        Invalidates the cached entry for this name so the next ``get_secret``
        returns the staged value, not a stale cache hit.
        """

        self._store[name] = value
        self._cache.pop(name, None)

    def get_secret(self, name: str) -> KeyVaultSecret:
        """Return the secret value object, honoring the 60-second cache.

        Raises ``SecretNotFound`` for missing secrets, matching the behavior
        of ``azure.core.exceptions.ResourceNotFoundError`` from the real
        SDK at a shape that does not require importing the azure namespace.
        Cache misses (expired or absent) fall through to the underlying
        store. Failed lookups are not cached.
        """

        now = time.monotonic()
        cached = self._cache.get(name)
        if cached is not None:
            secret, expires_at = cached
            if now < expires_at:
                return secret

        try:
            secret = KeyVaultSecret(name=name, value=self._store[name])
        except KeyError as exc:
            raise SecretNotFound(name) from exc

        self._cache[name] = (secret, now + self._CACHE_TTL_SECONDS)
        return secret

    async def get_secret_with_retry(
        self,
        name: str,
        *,
        stop: stop_base = RETRY_STOP,
        wait: wait_base = RETRY_WAIT,
    ) -> KeyVaultSecret:
        """Retry-wrapped variant of :meth:`get_secret`.

        Demonstrates the canonical transient-retry policy on a real-
        shaped code path: :func:`edlink_rostering.core.retry.with_retry` wraps
        the call so an ``httpx.ConnectError`` raised by
        :meth:`inject_transient_failures` triggers exponential backoff
        and a replay. ``SecretNotFound`` is NOT retried because the
        policy's exception classification excludes ``KeyError``
        subclasses (those are bugs, not transient failures).

        ``stop`` / ``wait`` default to the production policy. Tests
        pass ``wait=wait_none()`` so the retry loop runs without the
        1-30s exponential backoff that would otherwise dominate the
        suite's wall time.

        Production swap: this method's body becomes the call into the
        real ``SecretClient.get_secret_async``; the
        ``inject_transient_failures`` seam disappears and the policy
        keeps the same shape.
        """

        async def _call() -> KeyVaultSecret:
            if self._transient_failures_remaining > 0:
                self._transient_failures_remaining -= 1
                raise httpx.ConnectError(
                    "Synthetic Key Vault transient failure for retry testing."
                )
            return self.get_secret(name)

        return await with_retry(_call, stop=stop, wait=wait)


__all__ = ["KeyVaultClient", "KeyVaultSecret", "SecretNotFound"]
