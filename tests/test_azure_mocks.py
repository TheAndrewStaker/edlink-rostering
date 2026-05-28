"""Tests for the Azure mocks.

The Service Bus session-lock tests are the most important: the EdLink
rostering design rests on the per-LEA serialization guarantee, and a mock
that does not enforce it would let the sync worker pass with a contract
the production broker would refuse.
"""

from __future__ import annotations

import os

import pytest

from edlink_rostering.infrastructure.azure_mocks import (
    FunctionContext,
    KeyVaultClient,
    ServiceBusClient,
    Telemetry,
)
from edlink_rostering.infrastructure.azure_mocks.app_insights import MemorySink
from edlink_rostering.infrastructure.azure_mocks.key_vault import SecretNotFound
from edlink_rostering.infrastructure.azure_mocks.service_bus import (
    NoSessionAvailable,
    SessionLocked,
)


# ── Service Bus ───────────────────────────────────────────────────────────────


def test_service_bus_round_trip_single_session() -> None:
    bus = ServiceBusClient()
    bus.send_message("sync", b"hello", session_id="lea-001")

    with bus.receive_session("sync", session_id="lea-001") as receiver:
        messages = receiver.receive_messages(max_count=10)
        assert len(messages) == 1
        assert messages[0].body == b"hello"
        assert messages[0].session_id == "lea-001"
        receiver.complete_message(messages[0])

    assert bus.peek_session("sync", "lea-001") == 0


def test_service_bus_fifo_within_session() -> None:
    bus = ServiceBusClient()
    for i in range(3):
        bus.send_message("sync", f"msg-{i}".encode(), session_id="lea-001")

    with bus.receive_session("sync", session_id="lea-001") as receiver:
        messages = receiver.receive_messages(max_count=10)
        bodies = [m.body for m in messages]
        assert bodies == [b"msg-0", b"msg-1", b"msg-2"]
        for m in messages:
            receiver.complete_message(m)


def test_service_bus_session_lock_blocks_concurrent_receiver() -> None:
    """Two receivers cannot hold the same session simultaneously."""
    bus = ServiceBusClient()
    bus.send_message("sync", b"x", session_id="lea-001")

    with bus.receive_session("sync", session_id="lea-001"):
        with pytest.raises(SessionLocked):
            with bus.receive_session("sync", session_id="lea-001"):
                pass

    # After the outer context exits, the session is reclaimable.
    with bus.receive_session("sync", session_id="lea-001") as receiver:
        assert len(receiver.receive_messages(1)) == 1


def test_service_bus_different_sessions_can_be_held_concurrently() -> None:
    bus = ServiceBusClient()
    bus.send_message("sync", b"a", session_id="lea-001")
    bus.send_message("sync", b"b", session_id="lea-002")

    with bus.receive_session("sync", session_id="lea-001") as r1:
        with bus.receive_session("sync", session_id="lea-002") as r2:
            m1 = r1.receive_messages(1)
            m2 = r2.receive_messages(1)
            assert m1[0].body == b"a"
            assert m2[0].body == b"b"


def test_service_bus_abandon_returns_message_to_head() -> None:
    bus = ServiceBusClient()
    bus.send_message("sync", b"first", session_id="lea-001")
    bus.send_message("sync", b"second", session_id="lea-001")

    with bus.receive_session("sync", session_id="lea-001") as receiver:
        first = receiver.receive_messages(1)[0]
        receiver.abandon_message(first)

    # Reclaim the session; the abandoned message is at the head.
    with bus.receive_session("sync", session_id="lea-001") as receiver:
        messages = receiver.receive_messages(max_count=10)
        assert [m.body for m in messages] == [b"first", b"second"]


def test_service_bus_lock_release_returns_in_flight_messages() -> None:
    """If a session lock releases while a message is in-flight (received but
    not completed), the message returns to the head of the session for the
    next receiver. This is how Postgres-transaction rollback maps onto
    Service Bus retry semantics in production."""
    bus = ServiceBusClient()
    bus.send_message("sync", b"x", session_id="lea-001")

    with bus.receive_session("sync", session_id="lea-001") as receiver:
        msg = receiver.receive_messages(1)[0]
        # Simulate sync worker crash mid-transaction: do not complete, do not
        # abandon; just exit the context.
        assert msg.body == b"x"

    with bus.receive_session("sync", session_id="lea-001") as receiver:
        messages = receiver.receive_messages(1)
        assert messages[0].body == b"x"


def test_service_bus_receive_next_available_session() -> None:
    bus = ServiceBusClient()
    bus.send_message("sync", b"a", session_id="lea-001")
    bus.send_message("sync", b"b", session_id="lea-002")

    with bus.receive_session("sync") as receiver:
        assert receiver.session_id in {"lea-001", "lea-002"}


def test_service_bus_no_session_available_raises() -> None:
    bus = ServiceBusClient()
    with pytest.raises(NoSessionAvailable):
        with bus.receive_session("sync"):
            pass


# ── Key Vault ─────────────────────────────────────────────────────────────────


def test_key_vault_put_and_get() -> None:
    vault = KeyVaultClient()
    vault.put_secret("edlink-token-lea-001", "bearer-abc")
    assert vault.get_secret("edlink-token-lea-001").value == "bearer-abc"


def test_key_vault_missing_secret_raises() -> None:
    vault = KeyVaultClient()
    with pytest.raises(SecretNotFound):
        vault.get_secret("never-staged")


def test_key_vault_loads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYVAULT__EDLINK_TOKEN_LEA_999", "bearer-from-env")
    vault = KeyVaultClient()
    assert vault.get_secret("edlink-token-lea-999").value == "bearer-from-env"


def test_key_vault_get_secret_caches_within_ttl() -> None:
    """Second read of the same secret within the 60s TTL returns the cached
    value, not a fresh store lookup. Required because real Azure Key Vault
    rate-limits reads at ~2000 / 10s; the cache prevents the per-poll path
    from saturating that limit at fleet scale."""

    vault = KeyVaultClient()
    vault.put_secret("edlink-token-lea-001", "bearer-original")

    first = vault.get_secret("edlink-token-lea-001")

    # Mutate the underlying store without going through put_secret so the
    # cache invalidation path is not exercised. A cache hit must still
    # return the original value.
    vault._store["edlink-token-lea-001"] = "bearer-bypassed"

    second = vault.get_secret("edlink-token-lea-001")

    assert first.value == "bearer-original"
    assert second.value == "bearer-original"


def test_key_vault_put_secret_invalidates_cache() -> None:
    """``put_secret`` must drop the cached entry so a test or operator
    staging a new value sees it on the next read, regardless of TTL."""

    vault = KeyVaultClient()
    vault.put_secret("edlink-token-lea-001", "bearer-v1")
    assert vault.get_secret("edlink-token-lea-001").value == "bearer-v1"

    vault.put_secret("edlink-token-lea-001", "bearer-v2")
    assert vault.get_secret("edlink-token-lea-001").value == "bearer-v2"


def test_key_vault_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the 60s TTL elapses, the next read falls through to the
    store and re-caches. The TTL is enforced by ``time.monotonic``; the
    test drives the clock forward to avoid sleeping."""

    import time as time_module

    from edlink_rostering.infrastructure.azure_mocks import key_vault as kv_module

    fake_now = [1000.0]

    def _fake_monotonic() -> float:
        return fake_now[0]

    monkeypatch.setattr(kv_module.time, "monotonic", _fake_monotonic)

    vault = KeyVaultClient()
    vault.put_secret("edlink-token-lea-001", "bearer-original")

    first = vault.get_secret("edlink-token-lea-001")
    assert first.value == "bearer-original"

    # Advance past the 60s TTL boundary, then mutate the store directly.
    # The next read should miss the cache and pick up the new value.
    fake_now[0] += KeyVaultClient._CACHE_TTL_SECONDS + 1.0
    vault._store["edlink-token-lea-001"] = "bearer-after-expiry"

    second = vault.get_secret("edlink-token-lea-001")
    assert second.value == "bearer-after-expiry"

    # Silence unused-import warning.
    _ = time_module


# ── App Insights / Telemetry ──────────────────────────────────────────────────


def test_telemetry_captures_event() -> None:
    sink = MemorySink()
    telemetry = Telemetry(sinks=[sink])

    telemetry.track_event(
        "sync_completed",
        properties={"lea_id": "lea-001"},
        measurements={"event_count": 42.0},
    )

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.kind == "event"
    assert record.name == "sync_completed"
    assert record.properties == {"lea_id": "lea-001"}
    assert record.measurements == {"event_count": 42.0}


def test_telemetry_captures_metric() -> None:
    sink = MemorySink()
    telemetry = Telemetry(sinks=[sink])
    telemetry.track_metric("cursor_lag_days", 7.5)

    assert sink.records[0].kind == "metric"
    assert sink.records[0].measurements == {"cursor_lag_days": 7.5}


def test_telemetry_captures_exception() -> None:
    sink = MemorySink()
    telemetry = Telemetry(sinks=[sink])
    try:
        raise ValueError("upstream gone")
    except ValueError as exc:
        telemetry.track_exception(exc, properties={"lea_id": "lea-001"})

    record = sink.records[0]
    assert record.kind == "exception"
    assert record.properties["exception_type"] == "ValueError"
    assert record.properties["exception_message"] == "upstream gone"
    assert record.properties["lea_id"] == "lea-001"


# ── Function Context ──────────────────────────────────────────────────────────


def test_function_context_log_fields_carry_invocation_metadata() -> None:
    ctx = FunctionContext(function_name="sync_worker")
    fields = ctx.log_fields()
    assert fields["function_name"] == "sync_worker"
    assert fields["invocation_id"]  # auto-generated UUID
    assert fields["invocation_time"]  # ISO timestamp
