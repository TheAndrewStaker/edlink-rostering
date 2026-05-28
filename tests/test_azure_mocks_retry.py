"""End-to-end retry-policy tests against the Azure mocks.

The pure-policy classification tests (``tests/test_retry_policy.py``)
prove that ``RETRY_CONDITION`` matches ``httpx.ConnectError`` and
non-2xx ``httpx.Response`` objects. These tests close the gap that a
prior review called out: the policy was imported but not load-bearing
on any real-shaped code path. Here the ``with_retry`` helper wraps
:meth:`KeyVaultClient.get_secret_with_retry` and
:meth:`ServiceBusClient.send_message_with_retry`; synthetic-failure
injection on the mocks lets the test prove the wrapper replays after
a partner-side blip.

Tests use ``wait=wait_none()`` so backoff does not slow the suite. The
production policy's exponential-jitter wait is exercised in
``tests/test_retry_policy.py``.
"""

from __future__ import annotations

import httpx
import pytest
from tenacity import stop_after_attempt, wait_none

from edlink_rostering.core.retry import (
    RETRY_CONDITION,
    with_retry,
)
from edlink_rostering.infrastructure.azure_mocks.key_vault import (
    KeyVaultClient,
    SecretNotFound,
)
from edlink_rostering.infrastructure.azure_mocks.service_bus import (
    ServiceBusClient,
)


# ── Key Vault ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_key_vault_get_secret_with_retry_replays_after_transient_failures() -> None:
    """Two transient outages followed by a success: the wrapper retries through."""

    vault = KeyVaultClient()
    vault.put_secret("edlink-token-lea-a", "bearer-A")
    vault.inject_transient_failures(2)

    secret = await vault.get_secret_with_retry(
        "edlink-token-lea-a", wait=wait_none()
    )

    assert secret.value == "bearer-A"


@pytest.mark.asyncio
async def test_key_vault_get_secret_with_retry_gives_up_past_policy_budget() -> None:
    """The policy caps at 5 attempts; 6 failures exhaust it and raise."""

    vault = KeyVaultClient()
    vault.put_secret("edlink-token-lea-a", "bearer-A")
    vault.inject_transient_failures(10)

    with pytest.raises(httpx.ConnectError):
        await vault.get_secret_with_retry(
            "edlink-token-lea-a", wait=wait_none()
        )


@pytest.mark.asyncio
async def test_key_vault_get_secret_with_retry_does_not_retry_missing_secret() -> None:
    """``SecretNotFound`` is a real bug, not a transient failure.

    Pins the policy classification: the wrapper retries network-shape
    exceptions, not application-domain ``KeyError`` subclasses.
    """

    vault = KeyVaultClient()

    with pytest.raises(SecretNotFound):
        await vault.get_secret_with_retry(
            "nonexistent-secret", wait=wait_none()
        )


# ── Service Bus ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_bus_send_with_retry_replays_after_transient_failures() -> None:
    """Three transient outages followed by a successful enqueue."""

    bus = ServiceBusClient()
    bus.inject_transient_failures(3)

    msg = await bus.send_message_with_retry(
        queue_name="sync-pages",
        body=b'{"event": "ok"}',
        session_id="lea-a",
        wait=wait_none(),
    )

    assert msg.session_id == "lea-a"
    assert bus.peek_session("sync-pages", "lea-a") == 1


@pytest.mark.asyncio
async def test_with_retry_helper_accepts_tight_stop_for_test_use() -> None:
    """The helper accepts custom stop/wait so tests stay fast.

    Pins the helper's API surface used by ``send_message_with_retry``
    and ``get_secret_with_retry`` callers that want a tighter budget
    than the production 5-attempt default.
    """

    attempts = 0

    async def always_fails() -> int:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("always")

    with pytest.raises(httpx.ConnectError):
        await with_retry(
            always_fails,
            stop=stop_after_attempt(3),
            wait=wait_none(),
            retry=RETRY_CONDITION,
        )

    assert attempts == 3
