---
paths:
  - edlink_rostering/**/events/**/*.py
  - edlink_rostering/**/handlers/**/*.py
  - api/src/edlink_rostering/infrastructure/messaging/**/*.py
---

# Events

Event publishing and consumption discipline. The application uses an internal event bus for cross-bounded-context communication. Implementation may be Redis Streams, RabbitMQ, Kafka, or similar — the discipline is the same regardless.

## When to use events vs direct calls

Use events when:

- The consumer doesn't need a synchronous response
- Multiple consumers might want to react to the same fact
- The producer shouldn't know about consumers (decoupling)
- The work is durable and can be retried on failure
- The work crosses a bounded context (e.g., roster sync → notification service)

Use direct service calls when:

- The caller needs a response to continue
- The work is part of a transactional unit
- Latency matters (event hop adds delay)
- The work is internal to a bounded context

## Event shape

Every event has a consistent envelope:

```python
class EventEnvelope(BaseModel):
    event_id: UUID4  # globally unique, used for idempotency
    event_type: str  # e.g., "iep.amended", "roster.user.upserted"
    event_version: int  # schema version for this event_type
    occurred_at: datetime  # when the fact happened
    published_at: datetime  # when the event was put on the bus
    lea_id: LeaId  # multi-tenancy
    actor_id: str | None  # user or system actor
    correlation_id: UUID4  # for tracing across services
    causation_id: UUID4 | None  # event that caused this one (if any)
    payload: dict  # event-specific body
```

`event_id` is generated at the producer and persisted with the event for deduplication. **Consumers MUST treat event_id as the idempotency key.**

## Event types: past tense, dot notation

Event names describe what happened, in past tense. Use dot-separated namespace to indicate domain.

```python
# Good
"iep.amended"
"iep.goal.added"
"roster.user.upserted"
"roster.sync.completed"
"connector.auth.refreshed"
"compliance.deadline.approaching"

# Bad — imperative (sounds like a command)
"amend_iep"
"create_goal"

# Bad — present tense (ambiguous)
"iep_amending"
```

The naming makes event handlers read naturally: `on_iep_amended`, `on_roster_user_upserted`.

## Event payloads carry data, not commands

An event carries the facts of what happened. Consumers decide what to do with the facts.

```python
# Good — facts
{
    "event_type": "iep.goal.added",
    "payload": {
        "iep_id": "iep-abc",
        "goal_id": "goal-xyz",
        "goal_domain": "ReadingFluency",
        "added_by": "user-123"
    }
}

# Bad — command in event clothing
{
    "event_type": "iep.goal.added",
    "payload": {
        "iep_id": "iep-abc",
        "action": "send_email_to_case_manager",  # NO
        "email_to": "case.manager@district.example"  # NO
    }
}
```

If the consumer's job is to send an email, the consumer's code decides that based on the fact. Don't put procedural instructions in events.

## Producer responsibilities

The producer:

1. Generates a fresh `event_id` (UUID4)
2. Sets `occurred_at` to the time the fact actually happened (not the publish time)
3. Sets `lea_id` from the operation context
4. Sets `correlation_id` from the request context (or generates if not in a request)
5. Sets `causation_id` if this event was caused by another event
6. Validates the payload against the event's Pydantic schema
7. Persists the event idempotently (outbox pattern preferred for cross-transaction events)

### Outbox pattern for transactional events

Events that must be published as part of a database transaction use the outbox pattern: write the event to an `event_outbox` table in the same transaction as the domain change; a separate publisher worker reads the outbox and publishes to the bus, marking as published.

```python
async def amend_iep(iep_id: IEPId, changes: IEPChanges) -> IEP:
    async with session.begin():
        iep = await iep_repo.get_for_update(iep_id, lea_id)
        iep.apply_changes(changes)
        await iep_repo.save(iep)
        await event_outbox.append(
            event_type="iep.amended",
            payload={"iep_id": iep.id, "fields_changed": changes.fields()},
            lea_id=lea_id,
        )
    return iep
```

This guarantees: if the domain change persists, the event will eventually publish. If the transaction rolls back, no event is published.

## Consumer responsibilities

The consumer:

1. Idempotency: deduplicate by `event_id`. Track seen event_ids in a store with TTL.
2. Failure handling: retry transient failures; dead-letter on persistent ones.
3. Out-of-order tolerance: don't assume events arrive in the order they were published.
4. Schema versioning: support old `event_version` until producers fully migrate.

### Idempotency in consumers

```python
class IEPAmendedHandler:
    async def handle(self, envelope: EventEnvelope) -> None:
        # Idempotency check
        if await self.dedup_store.has_seen(envelope.event_id):
            log.info("event_already_processed", event_id=envelope.event_id)
            return

        # Process the event
        async with session.begin():
            await self.process(envelope)
            await self.dedup_store.mark_seen(envelope.event_id, ttl=86400 * 7)
```

The dedup store and the actual work must be in the same transaction, or you create the gap where work happens but dedup isn't recorded. Prefer storing the seen-event in the same database as your business state.

## Schema evolution

Events are forever — old events sit in dead-letter queues, in audit archives, in consumer replay logs. **Schemas evolve compatibly.**

Compatible changes:

- Adding optional fields (consumers ignore unknown)
- Adding new event types
- Adding new enum values (if consumers handle unknown gracefully)

Incompatible changes (bump `event_version`):

- Removing fields
- Renaming fields
- Changing field types
- Changing enum semantics

When bumping `event_version`, producers must continue emitting the old version until all consumers have migrated. This means **dual-publish during transitions.**

## Event payload PII

Same PII rules as logs apply to event payloads. **Events flow through systems you may not control directly** (queue infrastructure, ops dashboards, replay tools). Don't put student names in event payloads.

```python
# Good
payload = {"iep_id": iep.id, "student_id": iep.student_id, "fields_changed": ["services"]}

# Bad
payload = {"iep_id": iep.id, "student_name": "Alice Smith", "fields_changed": ["services"]}
```

If a consumer needs richer student context, it fetches it via the API at processing time (with the appropriate district scoping and audit logging).

## Webhook ingest also publishes events

Inbound webhooks from partners are validated, deduplicated, then translated into the application events. The webhook handler is thin:

```python
@router.post("/webhooks/ednition")
async def ednition_webhook(
    request: Request,
    body: bytes = Depends(get_raw_body),
    service: WebhookService = Depends(get_webhook_service),
) -> Response:
    # Verify signature, deduplicate (see security rule)
    await service.verify_ednition(request, body)

    # Translate to canonical event
    event = service.translate_ednition_event(body)

    # Publish to internal bus
    await event_publisher.publish(event)

    return Response(status_code=202)
```

The webhook handler doesn't do business logic. Translation + publish only. Business logic happens in event handlers.

## Dead-letter handling

Failures that exceed retry limits land in a dead-letter queue. Dead-letter contents are:

- Surveilled (alert when count exceeds threshold)
- Inspectable (manual replay tool)
- Diagnosable (full envelope + failure reason captured)

Don't dead-letter silently. Don't auto-discard. A failed event represents work the system has committed to do.

## Event sourcing vs event-carried state transfer

The application uses **event-carried state transfer** for most domain events: the event includes enough state to act on, but the source of truth remains the database.

The application is NOT event-sourced (where the events are the source of truth). Don't reach for event sourcing without compelling reason — it adds significant operational complexity and isn't necessary for the application's use cases.

## Naming conventions

Producers and consumers are named consistently:

- Event types: `domain.entity.verb` past-tense, e.g., `iep.goal.added`
- Producer functions: `publish_<event_type>` or `emit_<event_type>`
- Consumer classes: `<EventType>Handler`, e.g., `IEPAmendedHandler`
- Consumer functions: `on_<event_type>` or `handle_<event_type>`

## Testing event flows

Test event producers by asserting events are published:

```python
async def test_amend_iep_publishes_event(iep_service, event_capture):
    await iep_service.amend(iep_id, changes)
    events = event_capture.get("iep.amended")
    assert len(events) == 1
    assert events[0].payload["iep_id"] == iep_id
```

Test event consumers in isolation:

```python
async def test_iep_amended_handler_notifies_case_manager(handler, mock_notifier):
    envelope = build_envelope("iep.amended", {"iep_id": "abc", ...})
    await handler.handle(envelope)
    assert mock_notifier.notify.called
```

End-to-end event flows are integration-tested separately with a real bus.
