# Sync patterns

Patterns for keeping the application's data in sync with LEA source systems.

## Three sync channels

Every integration partner generally supports two or three channels. The framework should treat them as additive layers, not alternatives.

### Channel 1 — Real-time change events (webhooks)

The partner pushes events to the application when something changes. Lowest latency. Most reliable when it works. Fragile to webhook delivery failures.

- Clever pushes `student.created`, `student.updated`, `enrollment.deleted`, etc.
- ClassLink, EdLink, Ednition all support webhooks
- IEP system webhooks vary by vendor

### Channel 2 — Event cursor backfill (catch-up sync)

The application polls an events endpoint with a cursor, asking "what's happened since the last cursor I have?" Backstop for webhook delivery failures. Run on schedule (e.g., every 5 minutes) and after any detected webhook outage.

- Clever has `/v3.0/events?starting_after=cursor`
- EdLink Graph API supports change tracking (verify)
- Ednition has event-based delivery with rewind/replay built in

### Channel 3 — Full reconciliation (drift detection)

Periodic comparison between the application's local state and the partner's source-of-truth state. Catches drift that both Channel 1 and Channel 2 missed. Run nightly at minimum.

The reconciliation pattern depends on the partner and the data shape. Common options:

- **Push-event reconciliation:** compare event counts, rebuild on drift
- **Pull-snapshot reconciliation:** full snapshot pulled periodically, compared against canonical
- **File-transfer reconciliation:** full files exchanged via SFTP, compared against expected state

Design the reconciliation approach per partner. The next section discusses one pattern (tiered escalation) worth evaluating for bidirectional integrations.

## Tiered escalation reconciliation (pattern worth evaluating)

Documented here as a candidate approach for specific integration partners.

A flat-snapshot diff scales with total data size, not change rate. When drift is rare, most of the work is wasted on records that are in sync. Tiered escalation is one way to avoid that waste, starting cheap and escalating only on detected drift.

### L1 — Aggregate digest per group

Both sides hash a small summary of each school's state:

```python
def school_digest(school_id: str) -> SchoolDigest:
    return SchoolDigest(
        school_id=school_id,
        active_student_count=count,
        active_iep_count=iep_count,
        goal_count=goal_count,
        max_modified_at=max_modified,
        digest_hash=sha256(...),  # over the above fields
    )
```

Compare per-school digests across partner and local state. Match means in sync; no further work needed for this school. Mismatch means proceed to L2 for this school only.

**Bandwidth:** a few hundred bytes per school. Most schools should land here on any given day, assuming drift is genuinely rare.

### L2 — Per-record hash, mismatched groups only

For each school that disagreed at L1, fetch a paginated list of per-record hashes:

```python
def student_hashes(school_id: str) -> Iterable[StudentHash]:
    for student in students_in_school(school_id):
        yield StudentHash(
            student_id=student.id,
            last_modified_at=student.modified,
            record_hash=sha256(...),  # over the full student record
        )
```

Compare to the partner's equivalent. Match means this student is in sync. Mismatch means proceed to L3 for this student only.

**Bandwidth:** ~64 bytes per student, but only inside the few schools that drifted.

### L3 — Full record diff, drifted records only

For each student that disagreed at L2, fetch the full record on both sides and field-diff:

```python
def reconcile_student(student_id: str) -> ReconciliationResult:
    local = local_repo.get_student(student_id)
    remote = partner.get_student(student_id)
    return field_diff(local, remote)
```

Decide per-field which side wins. For most fields, partner wins (they're the source of truth). For application-owned fields (progress notes, AI annotations), the application wins.

**Bandwidth:** dozens to low hundreds of full records per cycle, not millions.

### When this pattern is worth the complexity

Tiered escalation pays off when **drift is rare** and **dataset is large enough that flat-diff cost matters**. If drift is common (active write-back integrations with frequent conflicts), the tiered approach adds overhead without saving much.

The pattern applies most usefully to **bidirectional integrations** where both sides can be authoritative for different fields. For Clever rostering (Clever is single source of truth), reconciliation is simpler — replay missed events from the cursor and you're back in sync. For IEP system write-back, where both the application and the IEP system write to the same student record, tiered escalation has more upside.

Design the actual reconciliation strategy per partner. Document the choice in `docs/partners/<partner>.md`. Revisit as drift rates become observable in production.

## Idempotency

Every external write operation must be safe to retry. Implement this at multiple layers:

### Outbound writes

Include an idempotency key in every write:

```python
def write_progress(student_id: str, goal_id: str, progress_data: dict) -> WriteResult:
    idempotency_key = compute_deterministic_id(student_id, goal_id, progress_data)
    return partner.write_progress(
        student_id=student_id,
        goal_id=goal_id,
        data=progress_data,
        idempotency_key=idempotency_key,
    )
```

The deterministic ID should be derived from the inputs so that retrying the same logical write produces the same key. Random UUIDs work but require persistence so retries can reuse them.

### Inbound webhooks

Persist `event_id` and reject duplicates:

```python
async def handle_webhook(event: PartnerEvent) -> None:
    if await event_log.has_processed(event.id):
        return  # already handled, accept the retry
    await event_log.mark_processed(event.id)
    await event_router.dispatch(event)
```

Acceptance of a duplicate event must return success — the partner's retry must see the same outcome as the original.

### Reconciliation writes

When reconciliation identifies drift and triggers a corrective write, the same idempotency key strategy applies. Reconciliation-triggered writes should be marked with a `source: reconciliation` tag in the audit log so they're distinguishable from event-triggered writes.

## Webhook reception scaffolding

Standard verification flow for every inbound webhook:

```python
async def webhook_handler(request: Request, partner: str) -> Response:
    # 1. Verify signature on raw body (constant-time comparison)
    raw_body = await request.body()
    signature_header = request.headers.get(SIGNATURE_HEADER[partner])
    if not verify_signature(raw_body, signature_header, partner):
        return Response(status_code=401)

    # 2. Check timestamp freshness (5 min window)
    timestamp = extract_timestamp(signature_header)
    if abs(time.time() - timestamp) > FRESHNESS_WINDOW_SECONDS:
        return Response(status_code=400)  # stale

    # 3. Idempotency check
    event = parse_event(raw_body, partner)
    if await event_log.has_processed(event.id):
        return Response(status_code=200)  # already handled

    # 4. Enqueue for processing (do not process synchronously)
    await event_queue.enqueue(event)
    await event_log.mark_received(event.id)

    # 5. Return 200 quickly so partner doesn't retry
    return Response(status_code=200)
```

Critical points:

- **Verify on raw bytes, not parsed body.** JSON deserialization can normalize whitespace and change the signature input.
- **Return 200 fast.** Don't do heavy work synchronously. Enqueue and process asynchronously.
- **Idempotency on event_id is mandatory.** Partners retry aggressively.
- **Process asynchronously.** If processing fails, the event is still in the queue for retry. Webhook responses should not depend on downstream success.

## Retry and backoff

Standard exponential backoff with jitter on every external call:

```python
@retry(
    retry=retry_if_exception_type(TransientError),
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def call_partner_api(...):
    ...
```

What counts as transient (retry) vs permanent (fail):
- **Transient:** HTTP 429, 502, 503, 504; network timeouts; connection errors
- **Permanent:** HTTP 4xx (except 429); validation errors; auth failures after refresh

Auth failures get one retry-after-refresh:

```python
async def authenticated_call(...):
    try:
        return await call_with_token(...)
    except HTTP401:
        await token_cache.invalidate(key)
        return await call_with_token(...)  # retry once with fresh token
```

## Circuit breakers

Long-running outages should circuit-break to avoid wasting work and noisy alerts:

```python
@circuit_breaker(
    failure_threshold=5,
    recovery_timeout=60,  # seconds
)
async def call_partner_api(...):
    ...
```

When a circuit is open, calls fail immediately with a `CircuitOpenError`. Periodic probes (one request per recovery period) test whether the partner has recovered.

## Audit logging

Every operation that touches student data must be logged. FERPA requires it.

```python
@audit_log(scope="student_data")
async def fetch_student(student_id: str) -> Student:
    ...
```

Minimum fields in each audit record:
- `timestamp`
- `actor` (system_user_id, district_admin_id, or system actor like "reconciliation")
- `operation` (read | write | delete)
- `resource_type` (student | iep | goal | progress_entry)
- `resource_id`
- `lea_id`
- `partner` (clever | edlink | direct_powerschool | etc.)
- `outcome` (success | failure | partial)

Retain audit logs at least 7 years. Some states require longer. Store in append-only storage with hash-chain integrity if possible.

