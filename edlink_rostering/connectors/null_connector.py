"""NullConnector.

Minimal implementation of the Connector protocol. Returns a synthetic
EventPage so the framework can be exercised end-to-end without any real
partner integration. Used to validate the protocol shape itself; real
connectors (EdLink) live in their own subdirectory.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import override

from edlink_rostering.canonical.entities import (
    CanonicalEntity,
    EntityType,
    Lea,
    LeaType,
    Student,
)
from edlink_rostering.connectors.protocol import (
    AckMode,
    AuthParams,
    AuthResult,
    Connector,
    EventPage,
    HealthStatus,
    InboundRequest,
    InboundResult,
    Layer1Result,
    ReconcileReport,
    WriteOp,
    WriteResult,
)
from edlink_rostering.core.types import Cursor, EventId, LeaId, StudentId
from edlink_rostering.events.envelope import NormalizedEvent, Operation


class NullConnector(Connector):
    """Connector that returns synthetic pages. No external dependencies."""

    name = "null"

    def __init__(self) -> None:
        self._authorized_leas: set[LeaId] = set()
        self._cursors: dict[LeaId, Cursor] = {}

    @override
    async def authorize_lea(
        self, lea_id: LeaId, params: AuthParams
    ) -> AuthResult:
        self._authorized_leas.add(lea_id)
        return AuthResult(
            success=True,
            lea_id=lea_id,
            scopes_granted=["read_roster"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    @override
    async def revoke_lea(self, lea_id: LeaId) -> None:
        self._authorized_leas.discard(lea_id)
        self._cursors.pop(lea_id, None)

    @override
    async def fetch_changes(
        self, lea_id: LeaId, since: Cursor
    ) -> EventPage:
        now = datetime.now(UTC)
        student = Student(
            id=StudentId("null-student-001"),
            lea_id=lea_id,
            given_name="Test",
            family_name="Student",
            grade="05",
        )
        event = NormalizedEvent(
            event_id=EventId(f"null-event-{lea_id}-001"),
            lea_id=lea_id,
            entity_type=EntityType.STUDENT,
            operation=Operation.CREATED,
            entity=student,
            source_connector=self.name,
            source_event_id="null-source-001",
            occurred_at=now,
            received_at=now,
        )
        return EventPage(
            events=[event],
            next_cursor=Cursor(value="null-cursor-001", observed_at=now),
            has_more=False,
            retrieved_at=now,
            layer_1_check=Layer1Result(
                ok=True,
                http_status=200,
                content_type="application/json",
                body_well_formed=True,
            ),
        )

    @override
    async def get_latest_cursor(self, lea_id: LeaId) -> Cursor:
        return Cursor(value="null-cursor-latest", observed_at=datetime.now(UTC))

    @override
    async def set_cursor(self, lea_id: LeaId, cursor: Cursor) -> None:
        self._cursors[lea_id] = cursor

    @override
    async def write(
        self,
        lea_id: LeaId,
        entity: CanonicalEntity,
        op: WriteOp,
        idempotency_key: str,
    ) -> WriteResult:
        return WriteResult(
            success=True,
            ack_mode=AckMode.SYNC,
            source_id="null-write-001",
            idempotency_key_used=idempotency_key,
        )

    @override
    async def handle_inbound(
        self, request: InboundRequest
    ) -> InboundResult:
        return InboundResult(events=[], follow_up_required=False)

    @override
    async def reconcile(self, lea_id: LeaId) -> ReconcileReport:
        now = datetime.now(UTC)
        return ReconcileReport(
            lea_id=lea_id,
            started_at=now,
            completed_at=now,
            in_sync_count=0,
            drift_count=0,
        )

    @override
    async def health(self) -> HealthStatus:
        return HealthStatus.GREEN

    @override
    async def list_authorized_leas(self) -> AsyncIterator[LeaId]:
        for lea_id in self._authorized_leas:
            yield lea_id


def make_test_lea() -> Lea:
    """Helper for tests: a synthetic LEA fixture."""
    return Lea(
        id=LeaId("null-lea-001"),
        name="Null Test School District",
        lea_type=LeaType.TRADITIONAL_DISTRICT,
        state="CA",
    )
