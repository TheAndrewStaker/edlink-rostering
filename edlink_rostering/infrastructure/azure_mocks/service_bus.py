"""In-memory mock of Azure Service Bus with faithful session semantics.

Why this exists: the EdLink rostering design uses
``session_id = lea_id`` as the per-LEA serialization mechanism. The POC needs
that guarantee to hold under concurrent access so the sync worker's
idempotency story matches the production design. A naive in-memory queue
without session locks would let two workers process the same LEA in parallel
and silently violate the design.

What the mock guarantees (matching ``azure-servicebus`` session-enabled queues
documented at https://learn.microsoft.com/azure/service-bus-messaging/message-sessions):

1. ``send_message`` enqueues a message under its ``session_id``. FIFO order is
   preserved per session.
2. ``receive_session`` claims a session by ID, or claims the next available
   session if no ID is given. While held, no other receiver can claim the same
   session. This is the session lock.
3. The session lock releases on context-manager exit (success or exception).
4. Messages have ``complete`` and ``abandon`` semantics. Complete removes the
   message from the session. Abandon returns it to the head of the session for
   redelivery.
5. An empty session is closed and recycled; a non-empty session whose lock
   expires is returned to the available pool.

What the mock does NOT cover (raise on use or document as out of scope):

- Dead-letter queues. The sync worker does not dead-letter; on terminal
  failure it writes a ``sync_jobs.status = failed`` row and the next poll
  retries via cursor replay.
- Lock auto-renewal. The mock's lock holds for the lifetime of the context
  manager regardless of duration. Production lock-renewal semantics land if
  the sync worker stays inside one receive for more than 60 seconds, which
  the page-per-transaction design avoids by construction.
- Cross-process sessions. The mock is single-process; production Service Bus
  brokers sessions across worker instances. The session-affinity contract is
  identical so the sync worker code does not change.
- Transactions across multiple Service Bus messages. Each message is its own
  unit; the LEA-scoped transactional batch is at the Postgres level, not
  Service Bus.
"""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity.stop import stop_base
from tenacity.wait import wait_base

from edlink_rostering.core.retry import RETRY_STOP, RETRY_WAIT, with_retry


@dataclass
class ServiceBusMessage:
    """A single message on the bus.

    Matches the subset of ``azure.servicebus.ServiceBusMessage`` that the sync
    worker reads: ``body``, ``session_id``, ``message_id``, ``enqueued_at``.
    """

    body: bytes
    session_id: str
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    properties: dict[str, Any] = field(default_factory=dict)


class ServiceBusSessionReceiver:
    """Receiver bound to one session.

    Yielded by :meth:`ServiceBusClient.receive_session` inside a context
    manager. The session lock is released when the context exits.
    """

    def __init__(self, queue: _SessionQueue, session_id: str) -> None:
        self._queue = queue
        self.session_id = session_id

    def receive_messages(
        self, max_count: int = 1, max_wait_time: float | None = None
    ) -> list[ServiceBusMessage]:
        """Return up to ``max_count`` messages from this session.

        Returns immediately with whatever is available (the mock does not
        block waiting for new messages). ``max_wait_time`` is accepted for
        interface parity with ``azure-servicebus`` and ignored.
        """

        _ = max_wait_time
        out: list[ServiceBusMessage] = []
        with self._queue.lock:
            queue = self._queue.sessions[self.session_id]
            while queue and len(out) < max_count:
                out.append(queue.popleft())
        self._queue._mark_in_flight(self.session_id, out)
        return out

    def complete_message(self, message: ServiceBusMessage) -> None:
        """Acknowledge the message. Removes it from the in-flight set."""

        self._queue._complete(self.session_id, message)

    def abandon_message(self, message: ServiceBusMessage) -> None:
        """Return the message to the head of the session for redelivery."""

        self._queue._abandon(self.session_id, message)


@dataclass
class _SessionQueue:
    """Internal state for a queue.

    sessions: session_id -> deque of pending messages.
    in_flight: session_id -> list of messages handed out but not yet
        completed or abandoned. On lock release, in-flight messages are
        treated as abandoned (returned to the head of the session).
    locks: session_id -> True if currently locked.
    lock: process-wide lock guarding the above maps.
    """

    sessions: dict[str, deque[ServiceBusMessage]] = field(
        default_factory=lambda: defaultdict(deque)
    )
    in_flight: dict[str, list[ServiceBusMessage]] = field(
        default_factory=lambda: defaultdict(list)
    )
    locks: dict[str, bool] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _mark_in_flight(
        self, session_id: str, messages: list[ServiceBusMessage]
    ) -> None:
        with self.lock:
            self.in_flight[session_id].extend(messages)

    def _complete(self, session_id: str, message: ServiceBusMessage) -> None:
        with self.lock:
            try:
                self.in_flight[session_id].remove(message)
            except ValueError:
                pass

    def _abandon(self, session_id: str, message: ServiceBusMessage) -> None:
        with self.lock:
            try:
                self.in_flight[session_id].remove(message)
            except ValueError:
                return
            self.sessions[session_id].appendleft(message)


class ServiceBusClient:
    """Process-local Service Bus broker.

    One instance backs all queues for a test run or POC demo. Threads or async
    tasks within the same process share its state through normal references.
    """

    def __init__(self) -> None:
        self._queues: dict[str, _SessionQueue] = defaultdict(_SessionQueue)
        self._transient_failures_remaining: int = 0

    def inject_transient_failures(self, count: int) -> None:
        """Make the next ``count`` retry-wrapped sends raise ``httpx.ConnectError``.

        The ``with_retry`` wrapper classifies ``httpx.ConnectError`` as
        retryable, so this method is the seam for testing that the
        production policy actually replays after a synthetic Service
        Bus outage. Affects :meth:`send_message_with_retry` only; the
        plain :meth:`send_message` path is unaffected so existing tests
        stay deterministic.
        """

        self._transient_failures_remaining = max(0, count)

    async def send_message_with_retry(
        self,
        queue_name: str,
        body: bytes,
        session_id: str,
        properties: dict[str, Any] | None = None,
        *,
        stop: stop_base = RETRY_STOP,
        wait: wait_base = RETRY_WAIT,
    ) -> ServiceBusMessage:
        """Retry-wrapped variant of :meth:`send_message`.

        Demonstrates the canonical transient-retry policy on a real-
        shaped code path:
        :func:`edlink_rostering.core.retry.with_retry` wraps the send so an
        ``httpx.ConnectError`` raised by
        :meth:`inject_transient_failures` triggers exponential backoff
        and a replay. ``stop`` / ``wait`` default to the production
        policy; tests pass ``wait=wait_none()`` to keep the suite
        fast. Production swap: this method's body becomes the call
        into the real ``ServiceBusSender.send_messages``; the
        injection seam disappears and the policy keeps the same shape.
        """

        async def _call() -> ServiceBusMessage:
            if self._transient_failures_remaining > 0:
                self._transient_failures_remaining -= 1
                raise httpx.ConnectError(
                    "Synthetic Service Bus transient failure for retry"
                    " testing."
                )
            return self.send_message(
                queue_name=queue_name,
                body=body,
                session_id=session_id,
                properties=properties,
            )

        return await with_retry(_call, stop=stop, wait=wait)

    def send_message(
        self,
        queue_name: str,
        body: bytes,
        session_id: str,
        properties: dict[str, Any] | None = None,
    ) -> ServiceBusMessage:
        """Enqueue a message under ``session_id`` on ``queue_name``."""

        msg = ServiceBusMessage(
            body=body, session_id=session_id, properties=properties or {}
        )
        queue = self._queues[queue_name]
        with queue.lock:
            queue.sessions[session_id].append(msg)
        return msg

    @contextmanager
    def receive_session(
        self, queue_name: str, session_id: str | None = None
    ) -> Iterator[ServiceBusSessionReceiver]:
        """Claim a session lock on ``queue_name``.

        If ``session_id`` is given, claim that specific session. If it is
        already locked, raise :class:`SessionLocked`. If ``session_id`` is
        None, claim the next available non-empty session; raise
        :class:`NoSessionAvailable` if none qualify.

        Releases the lock on context exit, returning any in-flight messages
        to the head of the session for redelivery.
        """

        queue = self._queues[queue_name]
        claimed = self._claim(queue, session_id)
        receiver = ServiceBusSessionReceiver(queue=queue, session_id=claimed)
        try:
            yield receiver
        finally:
            self._release(queue, claimed)

    def peek_session(self, queue_name: str, session_id: str) -> int:
        """Return the count of pending messages in a session.

        Useful for tests and operator visibility. Does not touch the session
        lock.
        """

        with self._queues[queue_name].lock:
            return len(self._queues[queue_name].sessions.get(session_id, ()))

    def _claim(self, queue: _SessionQueue, session_id: str | None) -> str:
        with queue.lock:
            if session_id is not None:
                if queue.locks.get(session_id):
                    raise SessionLocked(session_id)
                queue.locks[session_id] = True
                return session_id

            for sid, messages in queue.sessions.items():
                if messages and not queue.locks.get(sid):
                    queue.locks[sid] = True
                    return sid
            raise NoSessionAvailable(
                "No unlocked, non-empty session on this queue."
            )

    def _release(self, queue: _SessionQueue, session_id: str) -> None:
        with queue.lock:
            in_flight = queue.in_flight.pop(session_id, [])
            for msg in reversed(in_flight):
                queue.sessions[session_id].appendleft(msg)
            queue.locks[session_id] = False


class SessionLocked(RuntimeError):
    """Raised when ``receive_session`` is called for a session already held."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session {session_id!r} is already locked.")
        self.session_id = session_id


class NoSessionAvailable(RuntimeError):
    """Raised when ``receive_session`` is called with no session_id and no
    unlocked non-empty sessions exist."""
