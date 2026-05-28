"""Connector protocol.

The framework knows about one thing: a Connector. Each implementation owns its
own auth, payload mapping, and (until a second concrete connector exists)
webhook signature verification.

This protocol was re-derived from the actual EdLink Events API contract during
POC session 1. Key shape: fetch_changes returns one EventPage per call, not an
iterator of events. The page boundary is the HTTP response boundary, which is
also the LEA-scoped transactional batch boundary. See
docs/decisions/adr-004-connector-protocol-page-shape.md for the reasoning.

See architecture/connector-protocol.md and docs/design/edlink-oneroster-rostering.md
for the full spec.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from edlink_rostering.canonical.entities import CanonicalEntity
from edlink_rostering.core.types import Cursor, LeaId
from edlink_rostering.events.envelope import NormalizedEvent


# ── Supporting types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuthParams:
    """Inputs to authorize_lea. Shape varies by connector."""

    auth_code: str | None = None
    connection_token: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthResult:
    success: bool
    lea_id: LeaId
    scopes_granted: list[str]
    expires_at: datetime
    error: str | None = None


class WriteOp(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    UPSERT = "upsert"


class AckMode(str, Enum):
    SYNC = "sync"
    ASYNC = "async"
    PENDING = "pending"


@dataclass(frozen=True)
class WriteResult:
    success: bool
    ack_mode: AckMode
    source_id: str | None = None
    idempotency_key_used: str = ""
    error: str | None = None


@dataclass(frozen=True)
class InboundRequest:
    raw_body: bytes
    headers: dict[str, str]
    received_at: datetime


@dataclass(frozen=True)
class InboundResult:
    events: list[NormalizedEvent]
    follow_up_required: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ReconcileReport:
    lea_id: LeaId
    started_at: datetime
    completed_at: datetime
    in_sync_count: int
    drift_count: int
    drift_details: list[dict[str, object]] = field(default_factory=list)


class HealthStatus(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True)
class Layer1Result:
    """HTTP response integrity check.

    Layer 1 lives at the connector boundary because it is a check on the
    response object, not on the events. Sync worker reads this before running
    Layers 2-5.
    """

    ok: bool
    http_status: int
    content_type: str
    body_well_formed: bool
    error: str | None = None


@dataclass(frozen=True)
class EventPage:
    """One page of events returned by the partner's events endpoint.

    The page is the natural unit of work: the sync worker commits the page's
    events in one Postgres transaction and advances the cursor to next_cursor
    inside the same transaction. If has_more is true, the sync worker loops
    and processes the next page in a new transaction.
    """

    events: list[NormalizedEvent]
    next_cursor: Cursor
    has_more: bool
    retrieved_at: datetime
    layer_1_check: Layer1Result


# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class Connector(Protocol):
    """A pluggable integration source.

    Every connector implementation MUST satisfy this protocol. The framework
    dispatches all integration operations through it.

    Bulk-load (cold-start, cursor-past-retention recovery) is intentionally
    not part of this protocol in session 1. Bulk-load uses a different HTTP
    shape (per-resource endpoints, different pagination) and lumping it into
    the Connector protocol before the second connector exists would lock in
    an abstraction prematurely. See session 4 in the roadmap.
    """

    name: str

    async def authorize_lea(
        self, lea_id: LeaId, params: AuthParams
    ) -> AuthResult: ...

    async def revoke_lea(self, lea_id: LeaId) -> None: ...

    async def fetch_changes(
        self, lea_id: LeaId, since: Cursor
    ) -> EventPage: ...

    async def get_latest_cursor(self, lea_id: LeaId) -> Cursor: ...

    async def set_cursor(self, lea_id: LeaId, cursor: Cursor) -> None: ...

    async def write(
        self,
        lea_id: LeaId,
        entity: CanonicalEntity,
        op: WriteOp,
        idempotency_key: str,
    ) -> WriteResult: ...

    async def handle_inbound(
        self, request: InboundRequest
    ) -> InboundResult: ...

    async def reconcile(self, lea_id: LeaId) -> ReconcileReport: ...

    async def health(self) -> HealthStatus: ...

    # Async generator: ``def`` (not ``async def``) returning AsyncIterator
    # so an implementation using ``yield`` typechecks as a direct iterator
    # rather than a coroutine-returning-iterator. Caught by @override on
    # the connector implementations.
    def list_authorized_leas(self) -> AsyncIterator[LeaId]: ...
