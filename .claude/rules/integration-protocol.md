---
paths:
  - api/src/edlink_rostering/connectors/**/*.py
  - api/src/edlink_rostering/canonical/**/*.py
  - api/src/edlink_rostering/core/integration/**/*.py
---

# Integration protocol

The connector framework contract. Every external system integration — Ednition, EdLink, Clever, ClassLink, direct OneRoster, Ed-Fi, IEP system vendors, LMS via LTI — implements this contract.

Reference: `architecture/connector-framework.md`, `architecture/connector-protocol.md`, `docs/concepts/sync-patterns.md`, `docs/concepts/auth-patterns.md`.

## The Connector abstraction

Every connector implements a Python protocol:

```python
from typing import Protocol

class Connector(Protocol):
    """The integration protocol contract.

    Implementations are stateless except for a connector_id and the
    handle to scoped credentials. They never hold mutable state.
    All operations are async.
    """

    connector_id: ConnectorId
    partner: PartnerKind  # ednition, edlink, clever, classlink, edfi_direct, etc.
    lea_id: LeaId

    async def authenticate(self) -> Token: ...

    async def fetch_users(self, since: datetime | None = None) -> AsyncIterator[CanonicalUser]: ...

    async def fetch_orgs(self) -> AsyncIterator[CanonicalOrg]: ...

    async def fetch_classes(self, since: datetime | None = None) -> AsyncIterator[CanonicalClass]: ...

    async def fetch_enrollments(self, since: datetime | None = None) -> AsyncIterator[CanonicalEnrollment]: ...

    async def fetch_terms(self) -> AsyncIterator[CanonicalTerm]: ...

    async def health_check(self) -> ConnectorHealth: ...

    async def handle_inbound(self, request: InboundRequest) -> InboundResult: ...
```

The connector translates from partner-specific format to canonical. Above the connector layer, the rest of the application only sees canonical entities.

The full `Connector` contract, including `InboundRequest`/`InboundResult` types and the behavioral guarantees, lives in `architecture/connector-protocol.md`. That file is the canonical specification; this rule is the path-targeted summary.

## Authentication tokens cached per (partner, district)

Tokens are cached in Redis (or equivalent) keyed by `(partner, lea_id)`. Proactive refresh occurs when ≥80% of TTL has elapsed.

```python
async def authenticate(self) -> Token:
    cached = await self.token_cache.get(self.cache_key)
    if cached and cached.is_valid_with_buffer(seconds=300):
        return cached

    fresh = await self._fetch_fresh_token()
    await self.token_cache.set(self.cache_key, fresh, ttl=fresh.expires_in - 60)
    return fresh

@property
def cache_key(self) -> str:
    return f"token:{self.partner.value}:{self.lea_id}"
```

Tokens are encrypted at rest in the cache. **Never log token values.**

**EdLink is a provider-specific exception to the per-LEA token-exchange model.** EdLink owns each district's access token; we hold one application secret and fetch a district's token on demand keyed by its stable `integration_id` (`GET /api/v1/integrations`). Store `integration_id` per LEA, not a per-LEA secret we name or rotate. There is no per-LEA "rotate credential" operator flow; the only EdLink secret we rotate is the single application secret. Full model and reference URLs are in `docs/concepts/auth-patterns.md` ("EdLink integration access tokens"). Do not build per-LEA EdLink secret management against the generic pattern above.

## Idempotency on writes

Every write operation carries an idempotency key. Partners that support idempotency get it as a header; partners that don't get the key included in the payload (where the API allows custom fields).

```python
async def push_grade(
    self,
    grade: CanonicalGrade,
    idempotency_key: str | None = None,
) -> WriteResult:
    if idempotency_key is None:
        idempotency_key = str(uuid4())

    response = await self.http_client.post(
        "/scores",
        json=self.to_partner_format(grade),
        headers={"Idempotency-Key": idempotency_key},
    )
    return WriteResult.parse(response)
```

The idempotency key is preserved across retries so the partner can dedupe.

## Retries with backoff

Transient failures get retried with exponential backoff. **Never retry without backoff** — that's how partners get DDOS'd.

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    retry=retry_if_exception_type((ConnectorTransientError,)),
    reraise=True,
)
async def fetch_with_retry(self, path: str) -> dict:
    response = await self.http_client.get(path)
    if response.status_code in {429, 502, 503, 504}:
        raise ConnectorTransientError(f"transient failure: {response.status_code}")
    response.raise_for_status()
    return response.json()
```

Permanent failures (4xx other than 429) fail fast without retry.

## Webhook pipeline: framework-style vs connector-style responsibilities

Inbound webhooks flow through a clear two-stage discipline. **Verification is framework-style (lives inside the first connector for now, extracted to a shared ingress when the second connector ships); parsing is connector-style.** Per `architecture/connector-framework.md`, the shared `WebhookIngress` class is a deferred extraction; the discipline holds regardless of where the code physically lives.

**Framework-style work, in order:**

1. Look up the connector for the partner from the registry
2. Verify the signature header against the expected HMAC of the raw body
3. Check the timestamp is within the replay window (5 minutes default)
4. Reject (401 / 400) on any verification failure, before passing anything to the connector's parsing logic

**Connector parsing (`handle_inbound(InboundRequest) -> InboundResult`) does, in order:**

1. Parse the source-specific payload (signature is already verified by the time this runs)
2. Convert each event in the payload to a `NormalizedEvent`
3. Return `InboundResult(events=[...], follow_up_required=bool)`

**After `handle_inbound` returns:**

1. Framework-style idempotency: deduplicate each `NormalizedEvent` by `event_id` against the event log
2. Publish surviving events to the event bus (per `.claude/rules/events.md`)
3. If `follow_up_required=True`, schedule a backfill via the scheduler

**Discipline:**

- The connector MUST NOT verify signatures (the framework already did)
- The connector MUST NOT deduplicate inside `handle_inbound` (the framework owns dedup; double-dedup masks framework bugs)
- The connector MUST NOT publish events directly (the framework owns the bus)

See `architecture/connector-protocol.md` for the `InboundRequest` / `InboundResult` types and `.claude/rules/security.md` for HMAC verification specifics.

## Reconciliation

For each connector, periodic reconciliation against the partner. The right pattern depends on what the partner supports and what the application can afford to spend on it.

Common reconciliation patterns:

- **Push-event reconciliation.** Partner sends events; compare event counts and rebuild on drift.
- **Pull-snapshot reconciliation.** Periodically pull a full snapshot and compare against canonical.
- **File-transfer reconciliation.** Compare full files exchanged via SFTP against expected state.

Pattern worth evaluating for some partners (not assumed):

- **Tiered escalation.** Start cheap (top-level digest), escalate to per-entity hash on drift, escalate to full diff if hash mismatches persist. Saves cost when drift is rare. Worth designing into a connector only when the partner's API and the application's scale both justify it.

For the rostering MVP, start with the simplest pattern that meets the freshness requirement for each partner. Document the choice in `docs/partners/<partner>.md`. Revisit if drift rates or cost pressures warrant a more sophisticated approach.

```python
async def reconcile(self) -> ReconciliationResult:
    """Per-partner implementation. The reconciliation strategy depends on
    what the partner exposes and what freshness the application needs.

    Document the strategy in docs/partners/<partner>.md.
    """
    ...
```

Per-partner reconciliation strategy lives in `docs/partners/<partner>.md` and `docs/concepts/sync-patterns.md`.

## Canonical translation lives in the connector

Each connector has a `canonical.py` module that does partner→canonical translation. **Above the connector, the application only sees canonical types.**

```python
# connectors/oneroster/canonical.py
def canonical_user_from_oneroster(or_user: dict, lea_id: LeaId) -> CanonicalUser:
    """Translate OneRoster 1.2 user to canonical user."""
    return CanonicalUser(
        canonical_id=...,  # generated or resolved via identity resolver
        lea_id=lea_id,
        external_ids={
            "oneroster_sourcedid": or_user["sourcedId"],
            **{f"oneroster_{uid['type']}": uid["identifier"] for uid in or_user.get("userIds", [])},
        },
        given_name=or_user["givenName"],
        family_name=or_user["familyName"],
        preferred_first_name=or_user.get("preferredFirstName"),
        roles=[canonical_role(r) for r in or_user["roles"]],
        primary_org_id=or_user["primaryOrg"]["sourcedId"],
        status=canonical_status(or_user["status"]),
        last_modified=parse_iso8601(or_user["dateLastModified"]),
    )
```

Translation is pure (no I/O). All I/O is in the connector's API client. This makes translation easy to test.

## Identity resolution

Canonical IDs are stable within the application; external IDs from partners are preserved in `external_ids`. The identity resolver matches incoming records to existing canonical entities:

```python
class IdentityResolver:
    async def resolve_or_create_student(
        self,
        external_ids: dict[str, str],
        lea_id: LeaId,
        student_attributes: StudentAttributes,
    ) -> Student:
        # Try each external_id against the lookup table
        for key, value in external_ids.items():
            student = await self.student_repo.find_by_external_id(key, value, lea_id)
            if student:
                return student

        # No match; create new canonical student
        return await self.student_repo.create(lea_id, external_ids, student_attributes)
```

Identity resolution is multi-tenancy-aware (per `.claude/rules/multi-tenancy.md`): same `oneroster_sourcedid` in two districts is two different students.

## Health checks

Every connector exposes a health check. The health check verifies:

1. Credentials are valid (can fetch token)
2. Partner is reachable (a lightweight endpoint succeeds)
3. Last successful sync was within expected freshness window

```python
async def health_check(self) -> ConnectorHealth:
    try:
        await self.authenticate()
        await self.http_client.get(self.partner.health_endpoint)
        last_sync = await self.sync_state.last_success_at()
        is_fresh = last_sync and (datetime.now(UTC) - last_sync) < timedelta(hours=24)
        return ConnectorHealth(
            partner=self.partner,
            lea_id=self.lea_id,
            is_healthy=is_fresh,
            last_success_at=last_sync,
        )
    except Exception as e:
        return ConnectorHealth.unhealthy(self.partner, self.lea_id, reason=str(e))
```

Health checks feed the integration dashboard. Stale or failing connectors surface visibly.

## Connector registry

Connectors are registered, not imported directly:

```python
# connectors/registry.py
# MVP scope per ADR-001: only EdnitionConnector is built.
# Other entries are placeholders for post-MVP connectors and are commented
# out so the registry truthfully reflects what the framework can dispatch.
CONNECTORS: dict[PartnerKind, type[Connector]] = {
    PartnerKind.EDNITION: EdnitionConnector,
    # PartnerKind.EDLINK: EdLinkConnector,             # post-MVP, on-demand per ADR-001
    # PartnerKind.CLEVER: CleverConnector,             # post-MVP, only if Ednition coverage gap
    # PartnerKind.CLASSLINK: ClassLinkConnector,       # post-MVP
    # PartnerKind.ONEROSTER_DIRECT: OneRosterDirectConnector,  # post-MVP, direct OneRoster source
    # PartnerKind.EDFI_DIRECT: EdFiDirectConnector,    # post-MVP, direct Ed-Fi ODS consumption
}

def get_connector(partner: PartnerKind, lea_id: LeaId, config: ConnectorConfig) -> Connector:
    cls = CONNECTORS[partner]
    return cls(lea_id=lea_id, config=config)
```

This makes adding a new partner a matter of writing the implementation and registering it, not modifying call sites. Adding `EdLinkConnector` post-MVP, for example, is a new file plus an uncommented line.

## Rate limiting on outbound

Respect partner rate limits. Tokens-per-second per partner are configured; outbound calls go through a rate limiter:

```python
async def _send(self, request: HttpRequest) -> HttpResponse:
    await self.rate_limiter.acquire(self.partner)
    return await self.http_client.send(request)
```

Per-partner limits prevent one partner's lower threshold from blocking calls to others.

## Observability

Every connector call emits:

- A trace span with partner, operation, district
- A metric (partner_request_total, partner_request_duration, partner_error_total)
- Structured logs (with no PII per `.claude/rules/security.md`)

Dashboards aggregate by partner and by district.

## Connector configuration

Per-partner, per-district configuration lives in a structured store (database table or config service). Configuration includes:

- Credentials (encrypted)
- Base URL
- Webhook secrets
- Rate-limit overrides
- Per-district feature flags

Configuration changes are audit-logged.

## Testing connectors

Three layers:

1. **Unit tests** — pure translation logic with sample payloads
2. **Component tests** — full connector with mocked HTTP (using `respx`)
3. **Sandbox tests** — against partner sandbox accounts (run in CI weekly, not per-commit)

Sample payloads are committed in `tests/fixtures/oneroster/`, `tests/fixtures/ednition/`, etc. These come from real partner sandboxes (with PII scrubbed). The fixtures are the closest thing to a "spec" for the partner's actual implementation.

## Cross-references

- `architecture/connector-framework.md` — the broader architectural picture
- `architecture/connector-protocol.md` — protocol details and webhook patterns
- `docs/concepts/auth-patterns.md` — partner auth methods catalog
- `docs/concepts/sync-patterns.md` — sync model patterns
- `docs/partners/ednition.md`, `edlink.md`, `comparison.md` — partner-specific details
- OneRoster 1.2 — https://www.imsglobal.org/spec/oneroster/v1p2
- Ed-Fi 6.1 — https://docs.ed-fi.org/reference/data-exchange/data-standard/whats-new/whats-new-v61/
- `.claude/rules/security.md` — webhook verification, PII handling
- `.claude/rules/events.md` — connector events publish to internal bus
- `.claude/rules/multi-tenancy.md` — lea_id discipline
