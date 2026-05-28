# Connector framework architecture

The central design document. Read this before implementing any connector or making any architectural decision in the integration codebase.

The shape is small on purpose: a thin protocol the framework owns, plus per-partner connectors that own their own mechanics. Adding the second connector is the right time to extract shared scaffolding, not before. Build for the first concrete connector, find the seam when the second one shows up.

## Three-layer architecture

```
┌─────────────────────────────────────────────────────────┐
│  AI / RAG LAYER (downstream — out of scope here)        │
│  Vector DB, retrieval, prompt augmentation              │
└─────────────────────────────────────────────────────────┘
                            ▲
                            │   normalized event stream
                            │
┌─────────────────────────────────────────────────────────┐
│  CANONICAL DOMAIN LAYER                                 │
│  Normalized entities and event bus                      │
│  Student, Class, Enrollment, IEP, Goal, Progress, etc. │
└─────────────────────────────────────────────────────────┘
                            ▲
                            │   normalized writes
                            │
┌─────────────────────────────────────────────────────────┐
│  CONNECTOR LAYER                                        │
│  Pluggable per-source connectors                        │
│  EdLink, Ednition, Clever, Frontline IEP, PowerSchool, │
│  Canvas via LTI, NWEA MAP, SEIS over SFTP, etc.        │
└─────────────────────────────────────────────────────────┘
                            ▲
                            │   per-source protocols
                            │
        [LEA source systems: SIS, IEP, LMS, etc.]
```

The canonical domain layer is the seam between integration and product. Everything above it is product / AI concerns. Everything below it is integration concerns. Connectors translate; the canonical layer doesn't know or care which connector produced an entity.

## Connector protocol

The framework knows about one thing: a `Connector`. Each implementation owns its own auth, schema mapping, and signature verification. The framework owns the lifecycle, the event bus, the audit cross-cut, and the LEA state machine.

```python
class Connector(Protocol):
    """A pluggable integration source."""

    name: str  # e.g., "ednition", "frontline_iep", "powerschool_direct"

    async def authorize_lea(
        self, lea_id: LeaId, params: AuthParams
    ) -> AuthResult:
        """Begin or complete LEA-level authorization."""

    async def revoke_lea(self, lea_id: LeaId) -> None:
        """Tear down an LEA's authorization (FERPA / district-request)."""

    async def fetch_changes(
        self, lea_id: LeaId, since: Cursor | None
    ) -> AsyncIterator[NormalizedEvent]:
        """Pull changes since a cursor. Powers backfill and reconciliation."""

    async def write(
        self, lea_id: LeaId, entity: CanonicalEntity, op: WriteOp
    ) -> WriteResult:
        """Perform an outbound write. Used for write-back lanes."""

    async def handle_inbound(
        self, request: InboundRequest
    ) -> InboundResult:
        """Handle a webhook or inbound callback from this source.
        Signature verification, dedup, and replay protection are the
        connector's responsibility until a second connector exists with
        the same pattern. Then extract."""

    async def reconcile(
        self, lea_id: LeaId
    ) -> ReconcileReport:
        """Run reconciliation against the partner. Strategy is connector-
        specific per CLAUDE.md principle #10 and is documented in
        docs/partners/<partner>.md."""

    async def health(self) -> HealthStatus:
        """Report connector and source health for monitoring."""
```

That's the framework-visible surface. Everything else (token caching, OAuth flow, payload mapping, HMAC verification) lives inside the connector implementation. When a second connector arrives that shares mechanics with the first, the shared scaffolding is extracted at that point, not in advance.

**What is intentionally not in the framework yet:**

- `AggregatorConnector` base class for shared OAuth + webhook + cursor pagination. Add when the second aggregator (EdLink) ships and the shared shape is concrete.
- `AuthProvider` abstraction with multiple implementations. Add when the second auth pattern shows up (probably LTI 1.3 JWT assertion or SFTP key, not the second OAuth aggregator).
- `TokenCache` as a separate protocol. Lives inside the first connector until a second connector needs the same cache.
- `SchemaMapper` as a separate protocol. Lives inside each connector; no cross-connector reuse expected.
- `SignatureVerifier` as a separate protocol. Same logic.
- `WebhookReceiver` as a centralized ingress class. The first connector handles its own ingress route; centralize when the second connector adds its own.
- `ConnectorCapability` enum. Capability-based dispatch is useful when there are multiple connectors with overlapping coverage. With one connector it adds maintenance burden without paying rent.

This list is the answer to "why is this so small." The pattern these abstractions follow is well-known; the cost of getting them wrong before the second concrete case appears is higher than the cost of extracting them later from working code.

## Reconciliation

Per-connector with shared primitives, not a framework-level reconciliation engine. Per CLAUDE.md principle #10: each connector picks the pattern that fits its partner (push-event, pull-snapshot, file-transfer, tiered escalation) and implements `reconcile()` accordingly. The framework provides shared primitives (scheduling hook, drift telemetry shape, dead-letter routing for failed reconciliation runs) but does not impose a single reconciliation algorithm.

The failure mode this avoids: cross-cutting reconciliation logic that tries to be partner-agnostic ends up being neither correct for any partner nor cheap to maintain.

```python
class ReconciliationCoordinator:
    """Schedules and aggregates reconciliation runs across connectors.
    Per-connector `reconcile()` does the actual work; this class is the
    scheduling and telemetry shim."""

    async def run_nightly(self, lea_id: LeaId) -> list[ReconcileReport]:
        return [await c.reconcile(lea_id) for c in registry.for_lea(lea_id)]
```

`ReconcileReport` shape is connector-defined; the coordinator surfaces drift as structured events on the bus rather than interpreting the report itself.

## LEA authorization lifecycle

The framework treats LEA authorization as a first-class lifecycle. This is one of the few framework-level state machines because every connector goes through it and the transitions have FERPA implications.

```
1. INITIATED         LEA admin clicks "Add integration" in partner portal
                     → Partner redirects to the application with auth code or token
                     → Application stores partial state, awaits completion

2. AUTHORIZED        Token exchange complete, scopes confirmed
                     → Application schedules initial roster sync
                     → Cache LEA credentials

3. INITIAL_SYNCING   First-time full roster pull
                     → All entities imported, normalized, event-emitted

4. ACTIVE            Ongoing webhook + cursor backfill + nightly reconciliation
                     → Steady state

5. SUSPENDED         Auth failure, partner outage, or LEA admin action
                     → Pause syncs, alert ops, keep cached data

6. REVOKED           LEA admin removes the integration
                     → Stop syncs, retain audit log
                     → Per FERPA, may need to delete data after retention period

7. DELETED           Final state after retention period
                     → All LEA data purged from application systems
```

Each transition is an explicit event on the bus. The audit log records every transition with actor, timestamp, and outcome.

## Per-LEA tokens behind a single platform credential

The connector platform credential (`client_id` + `client_secret` at Clever, Ednition, EdLink) is **one global thing**, but it issues **per-LEA tokens** that scope to that LEA's data only.

```
Platform Credential (one global)
    ↓
    ├──── LEA A token (scopes A only)
    ├──── LEA B token (scopes B only)
    ├──── LEA C token (scopes C only)
    └──── ... (one per LEA)
```

Token cache keys are always `(connector_name, lea_id)`. Never share tokens across LEAs. Never cache tokens with a key that omits `lea_id`. This is enforced at the cache interface inside each connector; the framework does not own the cache yet.

## Event bus

Normalized events emitted by every connector flow through a single bus:

```python
class NormalizedEvent:
    event_id: str  # globally unique, idempotency key
    lea_id: LeaId
    entity_type: EntityType
    operation: str  # created | updated | deleted
    entity: CanonicalEntity
    source_connector: str
    source_event_id: str  # partner's event ID, for tracing
    occurred_at: datetime
    received_at: datetime
```

Downstream consumers (AI/RAG ingestion, audit log, search indexer) subscribe to the bus. The connector layer is the only producer. Idempotency is enforced at the framework boundary using `event_id`; connectors do not dedup.

**Transport.** Candidates: Redis Streams, Kafka, RabbitMQ, cloud-managed. The event envelope shape above is transport-agnostic.

## Aggregator vs direct: how one abstraction handles both

Aggregator connectors (EdLink, Ednition) wrap many sources behind one API. From the framework's perspective, they're still just one connector. The "many sources" is hidden inside the connector's implementation.

```python
class EdnitionConnector(Connector):
    """Single connector, wraps many SIS and LMS sources."""
    name = "ednition"

class PowerSchoolDirectConnector(Connector):
    """Direct integration with PowerSchool API. One source."""
    name = "powerschool_direct"
```

Product code asks the registry "which connector serves LEA 12345 for rostering?" and gets back the appropriate connector regardless of whether it's aggregator-backed or direct. Per-LEA routing rules live in the registry config, not in product code.

This is what makes the build-vs-buy choice reversible. Adding a direct PowerSchool connector later doesn't require changing product code; only the connector registry and the per-LEA routing rules.

## Audit and security cross-cut

Every operation that touches LEA data goes through the audit-log cross-cut (per `.claude/rules/security.md`). The framework provides the decorator; connectors call it on every read, write, and lifecycle transition. PII never appears in application logs (per the same rule); identifiers and outcomes are what get logged.

Webhook signature verification, replay protection, and idempotency are the connector's responsibility for now. The first connector establishes the pattern; the second one is the trigger to extract a shared `WebhookIngress` or equivalent.

## What's distinctive about the EdTech context

1. **Per-LEA-scoped tokens behind a single platform credential.** Most B2B integration patterns tokenize per relationship; EdTech aggregators tokenize per customer of the relationship.
2. **First-class aggregator connectors.** Aggregators (Clever, ClassLink, EdLink, Ednition) dominate the EdTech rostering pattern. The framework treats one aggregator connector as effectively a many-source meta-connector.
3. **LTI 1.3 launch flow.** Specific to the LMS lane; OIDC handshake + signed launch JWTs + service-call client_assertion. Different enough from OAuth client_credentials that it justifies its own connector when LTI work begins.
4. **Per-LEA authorization state machine.** Authorization is per-customer, lifecycle-managed (INITIATED → AUTHORIZED → ACTIVE → SUSPENDED → REVOKED → DELETED).

## Implementation priorities (MVP to production)

### MVP: Rostering

Minimum viable for a school-start deadline:

1. `Connector` protocol with strict contract conformance
2. One concrete connector implementation (Ednition first per ADR-001)
3. Canonical entities: `Lea`, `School`, `Student`, `Teacher`, `Class`, `Enrollment`
4. Event bus emitting `RosterChanged` events
5. Per-connector reconciliation (one pattern, the simplest that meets freshness)
6. LEA authorization state machine
7. Audit logging cross-cut
8. Monitoring and health checks

What's deferred until a second concrete connector arrives:

- `AggregatorConnector` base class extraction
- `AuthProvider` abstraction with multiple implementations
- Centralized `WebhookIngress` / `SignatureVerifier`
- `ConnectorCapability` enum and capability-based dispatch
- Standalone `TokenCache` and `SchemaMapper` protocols
- Tiered escalation reconciliation patterns

### Post-MVP

- Frontline IEP connector
- PowerSchool Special Programs connector
- SEIS SFTP connector (for California districts)
- LTI 1.3 platform integration (Canvas, Schoology, etc.)
- NWEA MAP, iReady, Renaissance direct integrations
- Extraction of shared scaffolding into base classes and abstractions, driven by what the second through fourth connectors actually share

See `architecture/connector-protocol.md` for the canonical event and inbound-request types.
