"""NormalizedEvent envelope.

Every connector emits events in this shape. Downstream consumers (AI ingestion,
audit log indexer, search indexer) subscribe to the bus and consume these
without knowing which connector produced them. The envelope is transport-
agnostic so the event bus transport choice (Q-019 in founder-decisions.md)
doesn't ripple through the connectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from edlink_rostering.canonical.entities import CanonicalEntity, EntityType
from edlink_rostering.core.types import EventId, LeaId


class Operation(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


@dataclass(frozen=True)
class NormalizedEvent:
    event_id: EventId  # globally unique, framework-level idempotency key
    lea_id: LeaId  # multi-tenancy scope; required on every event
    entity_type: EntityType
    operation: Operation
    entity: CanonicalEntity
    source_connector: str  # name of the connector that produced this event
    source_event_id: str  # partner's event ID, for tracing back to source
    occurred_at: datetime  # when the change happened at source (UTC)
    received_at: datetime  # when the application observed the change (UTC)
