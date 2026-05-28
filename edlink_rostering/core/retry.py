"""Canonical transient-retry policy for outbound HTTP.

A connector framework will hit transient failures from partner APIs:
TCP resets, slow upstreams, 429 throttles, transient 5xx. The right
shape for these is exponential backoff with jitter, capped at a small
number of attempts, and applied at the smallest scope that fully
covers the network call (so application-level state is not partially
mutated by a retry).

The module exports the three tenacity bits (``RETRY_STOP``,
``RETRY_WAIT``, ``RETRY_CONDITION``) plus a thin ``with_retry``
async helper. The helper exists so callers do not duplicate the
``async for attempt in AsyncRetrying(...)`` boilerplate at every call
site. Production connector clients use it; the in-memory Azure mocks
demonstrate the retry path (see
:meth:`edlink_rostering.infrastructure.azure_mocks.key_vault.KeyVaultClient.get_secret_with_retry`
and
:meth:`edlink_rostering.infrastructure.azure_mocks.service_bus.ServiceBusClient.send_message_with_retry`),
which makes the policy load-bearing today rather than only on the
production HTTP client's eventual landing.

Policy summary:

* **5 attempts max** (1 initial + 4 retries). Past that, the upstream
  is genuinely down and the operator should see the failure rather
  than the connector pretending to make progress.
* **Exponential backoff with jitter** (1s base, 30s cap in production;
  tests pass ``wait=wait_none()`` so the suite stays fast). Jitter
  smooths thundering-herd recovery after a partner outage clears.
* **Retry on**: ``httpx.TransportError`` (connection reset, DNS, TLS),
  ``httpx.TimeoutException`` (read/write timeout), and HTTP responses
  with status codes 429 or 5xx (via ``_is_retryable_response``).
* **Do NOT retry on**: 4xx other than 429 (client error, retry will
  not help), or any non-network exception (those are bugs, not
  transient failures).

Tested in ``tests/test_retry_policy.py`` (policy classification) and
``tests/test_azure_mocks_retry.py`` (end-to-end wrap on the mocks).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.retry import retry_base
from tenacity.stop import stop_base
from tenacity.wait import wait_base


RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


T = TypeVar("T")


def _is_retryable_response(response: object) -> bool:
    """True when an HTTP response merits a transient-retry."""

    return (
        isinstance(response, httpx.Response)
        and response.status_code in RETRYABLE_STATUSES
    )


RETRY_STOP: stop_base = stop_after_attempt(5)
RETRY_WAIT: wait_base = wait_exponential_jitter(initial=1, max=30)
RETRY_CONDITION: retry_base = retry_if_exception_type(
    (httpx.TransportError, httpx.TimeoutException)
) | retry_if_result(_is_retryable_response)


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    stop: stop_base = RETRY_STOP,
    wait: wait_base = RETRY_WAIT,
    retry: retry_base = RETRY_CONDITION,
) -> T:
    """Run ``operation`` under the canonical transient-retry policy.

    Default args match the production policy; tests inject a tighter
    ``stop`` / ``wait`` to keep the suite fast. The helper exists so
    call sites do not duplicate the ``async for attempt in
    AsyncRetrying(...)`` boilerplate.

    ``retry_if_result`` matches when the result satisfies the policy
    (e.g. a 503 ``httpx.Response``). The pattern from tenacity's docs
    is to set the result back on the attempt state after the with-block
    so the next iteration's retry condition can read it.
    """

    async for attempt in AsyncRetrying(
        stop=stop, wait=wait, retry=retry, reraise=True
    ):
        with attempt:
            result: T = await operation()
        outcome = attempt.retry_state.outcome
        if outcome is not None and not outcome.failed:
            attempt.retry_state.set_result(result)
            return result
    raise RuntimeError("with_retry: AsyncRetrying exited without a result")


__all__ = [
    "RETRYABLE_STATUSES",
    "RETRY_CONDITION",
    "RETRY_STOP",
    "RETRY_WAIT",
    "with_retry",
]
