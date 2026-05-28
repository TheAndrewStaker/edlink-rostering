# Connector protocol specification

The interface every connector implements. Strict contract. The framework dispatches all integration operations through this protocol; deviation breaks the abstraction.

This is the file Dev agents reference when implementing a new connector. It's also the file QA agents use to write contract tests.

## Protocol definition

```python
from typing import Protocol, AsyncIterator
from datetime import datetime

class Connector(Protocol):
    """Pluggable integration source.

    Every connector implementation MUST satisfy this protocol.
    The connector owns its own auth, payload mapping, and (for now)
    signature verification. Shared abstractions (AuthProvider,
    TokenCache, SchemaMapper, SignatureVerifier) are not part of
    the framework yet; they are extracted from the first concrete
    connector when the second one is added, per
    architecture/connector-framework.md. The ConnectorCapability enum
    is similarly deferred until the framework dispatches across
    multiple connectors with overlapping coverage.
    """

    name: str

    # ─── LEA authorization lifecycle ───

    async def authorize_lea(
        self,
        lea_id: LeaId,
        params: AuthParams,
    ) -> AuthResult:
        """Begin or complete authorization for a district.

        For OAuth-redirect flows, `params` may contain an auth code
        from a callback. For self-serve flows, `params` may contain
        an admin-issued connection token.

        Postcondition on success:
        - Token stored in token cache under (self.name, lea_id)
        - LEA state machine transitioned to AUTHORIZED
        - First sync scheduled
        """
        ...

    async def revoke_lea(self, lea_id: LeaId) -> None:
        """Tear down authorization for an LEA.

        Triggered by:
        - LEA admin removing the integration from the partner portal
        - Application-side revocation request
        - Retention-policy expiry

        Postcondition:
        - Token removed from cache
        - LEA state machine transitioned to REVOKED
        - Future operations against this LEA fail fast
        """
        ...

    # ─── Sync operations ───

    async def fetch_changes(
        self,
        lea_id: LeaId,
        since: Cursor | None = None,
    ) -> AsyncIterator[NormalizedEvent]:
        """Pull changes since a cursor.

        Used for:
        - Initial sync (since=None, full backfill)
        - Webhook gap recovery (since=last_cursor_before_outage)
        - Scheduled catch-up (since=last_cursor)

        Yields NormalizedEvent in chronological order.
        Final yielded event's cursor is the new cursor to persist.

        MUST be idempotent: re-running with the same `since` MUST
        yield the same events (modulo new changes since last call).
        """
        ...

    async def write(
        self,
        lea_id: LeaId,
        entity: CanonicalEntity,
        op: WriteOp,
        idempotency_key: str,
    ) -> WriteResult:
        """Outbound write to the source system.

        Only called for connectors that support write-back.
        idempotency_key is provided by the caller; connector
        MUST forward it to the source if the source supports it.

        Source-side write semantics vary:
        - Some sources ACK synchronously (most APIs)
        - Some sources ACK asynchronously via webhook (some IEP systems)
        - WriteResult MUST indicate which mode was used.
        """
        ...

    # ─── Inbound webhook handling ───

    async def handle_inbound(
        self,
        request: InboundRequest,
    ) -> InboundResult:
        """Process a webhook from this source.

        Signature verification happens in framework-style code before
        this method is called. By the time handle_inbound runs,
        authenticity is established. Until a second connector exists,
        the connector itself owns the verification step; the discipline
        (framework-style verifies, connector-style parses) holds
        regardless of where the code physically lives.

        Connector's job:
        - Parse source-specific payload
        - Convert to NormalizedEvent
        - Return events for the framework to publish to the event bus

        MUST NOT publish events directly. The framework owns the bus.
        """
        ...

    # ─── Reconciliation ───

    async def reconcile(
        self,
        lea_id: LeaId,
        scope: ReconcileScope | None = None,
    ) -> ReconcileReport:
        """Run reconciliation against the partner.

        The reconciliation strategy is connector-specific (push-event,
        pull-snapshot, file-transfer, or tiered escalation). The chosen
        pattern is documented in docs/partners/<partner>.md.

        Connectors that support tiered escalation may accept an optional
        `tier` parameter in their concrete implementation; the base
        protocol just asks "run reconciliation" and returns a report.

        `scope` narrows the reconciliation to specific schools or records
        when supported by the connector.
        """
        ...

    # ─── Operations ───

    async def health(self) -> HealthStatus:
        """Report connector and source health.

        MUST be fast (<1 second). Used for monitoring and alerting.

        Returns:
        - GREEN: all operations succeeding
        - YELLOW: degraded (slow, partial failures)
        - RED: source unreachable or auth broken
        """
        ...

    async def list_authorized_leas(self) -> AsyncIterator[LeaId]:
        """Enumerate all LEAs authorized through this connector."""
        ...
```

## Supporting types

```python
@dataclass
class AuthParams:
    """Inputs to authorize_lea. Shape varies by connector."""
    auth_code: str | None = None  # for OAuth redirect flows
    connection_token: str | None = None  # for partner-issued tokens
    sftp_credentials: SftpCredentials | None = None  # for SFTP connectors
    extra: dict = field(default_factory=dict)

@dataclass
class AuthResult:
    success: bool
    lea_id: LeaId
    scopes_granted: list[str]
    expires_at: datetime
    error: str | None = None

class Cursor(str):
    """Opaque pagination cursor. Format is connector-specific."""

class WriteOp(Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    UPSERT = "upsert"

@dataclass
class WriteResult:
    success: bool
    ack_mode: AckMode  # SYNC | ASYNC | PENDING
    source_id: str | None  # the source's ID for the written record
    idempotency_key_used: str
    error: WriteError | None = None

class AckMode(Enum):
    SYNC = "sync"      # ack received in this response
    ASYNC = "async"    # ack will come via webhook
    PENDING = "pending"  # source queued but won't ack

@dataclass
class InboundRequest:
    raw_body: bytes
    headers: dict[str, str]
    received_at: datetime

@dataclass
class InboundResult:
    events: list[NormalizedEvent]
    follow_up_required: bool  # if True, framework should trigger a backfill
    error: str | None = None

class ReconcileTier(Enum):
    """Optional. Used only by connectors that implement tiered-escalation
    reconciliation. Most connectors use simpler patterns (push-event,
    pull-snapshot, or file-transfer) and don't carry a tier."""
    AGGREGATE_DIGEST = 1
    PER_RECORD_HASH = 2
    FULL_DIFF = 3

@dataclass
class ReconcileScope:
    school_ids: list[SchoolId] | None = None
    entity_ids: list[str] | None = None

@dataclass
class ReconcileReport:
    tier: ReconcileTier | None  # None for non-tiered strategies
    lea_id: LeaId
    started_at: datetime
    completed_at: datetime
    in_sync_count: int
    drift_count: int
    drift_details: list[DriftRecord]
    next_tier_recommended: bool  # only meaningful for tiered strategies

class HealthStatus(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
```

## Capability dispatch (deferred)

The first version of the framework dispatches to a single connector per LEA, looked up by name in the registry. A `ConnectorCapability` enum and capability-based dispatch are deferred until the framework actually has multiple connectors with overlapping coverage; pre-building it adds maintenance burden without paying rent. Per `architecture/connector-framework.md`.

When capability dispatch is added, the shape is straightforward: each connector declares the capabilities it supports, and the framework checks before dispatching:

```python
# Future shape (not implemented in MVP)
async def write_grade(...):
    connector = registry.select(lea_id, capability=ConnectorCapability.WRITE_GRADES)
    if not connector:
        raise CapabilityNotAvailable(capability=ConnectorCapability.WRITE_GRADES)
    return await connector.write(...)
```

## Behavioral contract

Implementations MUST satisfy these properties:

### Idempotency

- `fetch_changes(since=X)` returns the same events for the same X (modulo new changes after the cursor).
- `write(..., idempotency_key=K)` performs the underlying write at-most-once for K.
- `handle_inbound` is called by the framework only after framework-level deduplication; connector MUST NOT also deduplicate (would mask framework bugs).

### Determinism

- `to_canonical(record)` returns the same canonical entity for the same source record. No timestamps, no random IDs from the mapper.
- Reconciliation digests at L1 and L2 are deterministic functions of the record set.

### Failure modes

- All operations raise typed exceptions inheriting from `ConnectorError`.
- `TransientError` for network / 5xx / rate-limit failures (framework retries).
- `PermanentError` for auth / 4xx / validation failures (framework does not retry, alerts).
- Connector MUST NOT retry internally. Retry is the framework's job.

### Resource management

- Connectors MUST close all sessions, files, and SFTP connections after use.
- Connectors MUST NOT hold global state. All state lives in framework-provided stores (token cache, event log, etc.).

## Contract tests

QA agents implement contract tests that any new connector must pass. Located in `tests/connector_contract/`:

```python
@pytest.mark.connector_contract
class ConnectorContractTests:
    """Every connector must pass these tests."""

    connector: Connector  # set per-test-class

    async def test_capabilities_declared(self):
        assert self.connector.capabilities, "Connector must declare at least one capability"

    async def test_health_returns_in_under_one_second(self):
        start = time.time()
        await self.connector.health()
        assert time.time() - start < 1.0

    async def test_fetch_changes_with_none_cursor_returns_iterator(self):
        events = self.connector.fetch_changes(lea_id=test_district, since=None)
        assert hasattr(events, "__aiter__")

    async def test_fetch_changes_is_idempotent(self):
        events_1 = [e async for e in self.connector.fetch_changes(
            test_district, since=test_cursor
        )]
        events_2 = [e async for e in self.connector.fetch_changes(
            test_district, since=test_cursor
        )]
        assert events_1 == events_2

    async def test_write_idempotency_key_respected(self):
        result_1 = await self.connector.write(
            test_district, test_entity, WriteOp.CREATE, "test-key-1"
        )
        result_2 = await self.connector.write(
            test_district, test_entity, WriteOp.CREATE, "test-key-1"
        )
        assert result_1.source_id == result_2.source_id

    # ... etc
```

## Mocking and sandbox

Each connector MUST provide a `MockConnector` implementation for downstream testing:

```python
class MockEdnitionConnector(EdnitionConnector):
    """In-memory implementation for testing downstream code without
    hitting Ednition's sandbox."""
    ...
```

The framework includes a `connector_factory` that returns the mock in test environments and the real connector in prod.
