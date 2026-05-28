"""End-to-end test of NullConnector through the framework boundary.

Exercises the full read path: authorize an LEA, fetch a page, assert the
EventPage and NormalizedEvent envelopes carry everything a downstream consumer
needs. This is the integration-test shape that the EdLink connector inherits.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from edlink_rostering.canonical.entities import EntityType, Student
from edlink_rostering.connectors import NullConnector
from edlink_rostering.connectors.null_connector import make_test_lea
from edlink_rostering.connectors.protocol import AuthParams
from edlink_rostering.core.types import Cursor
from edlink_rostering.events.envelope import Operation


@pytest.mark.asyncio
async def test_authorize_then_fetch_then_assert_event_shape() -> None:
    connector = NullConnector()
    lea = make_test_lea()

    # 1. Authorize the LEA.
    auth_result = await connector.authorize_lea(lea.id, AuthParams())
    assert auth_result.success

    # 2. LEA shows up in the authorized-LEAs enumeration.
    authorized = [lid async for lid in connector.list_authorized_leas()]
    assert lea.id in authorized

    # 3. Bootstrap a cursor for this LEA.
    seed_cursor = await connector.get_latest_cursor(lea.id)
    assert isinstance(seed_cursor, Cursor)

    # 4. Fetch one page of changes.
    page = await connector.fetch_changes(lea.id, since=seed_cursor)
    assert page.layer_1_check.ok
    assert len(page.events) == 1

    event = page.events[0]

    # 5. Envelope carries lea_id (the multi-tenancy scope, required on every event).
    assert event.lea_id == lea.id

    # 6. Source attribution: which connector and which source event ID, for tracing.
    assert event.source_connector == "null"
    assert event.source_event_id

    # 7. Idempotency key is present (framework dedups by event_id).
    assert event.event_id

    # 8. Entity is canonical (a Student dataclass), not a partner-specific dict.
    assert event.entity_type == EntityType.STUDENT
    assert isinstance(event.entity, Student)
    assert event.entity.lea_id == lea.id  # entity itself is also lea_id scoped

    # 9. Operation is one of the documented values.
    assert event.operation == Operation.CREATED

    # 10. Cursor advanced.
    assert page.next_cursor.value
    assert page.has_more is False


@pytest.mark.asyncio
async def test_revoke_removes_lea_from_authorized_list() -> None:
    connector = NullConnector()
    lea = make_test_lea()

    await connector.authorize_lea(lea.id, AuthParams())
    await connector.revoke_lea(lea.id)

    authorized = [lid async for lid in connector.list_authorized_leas()]
    assert lea.id not in authorized


@pytest.mark.asyncio
async def test_set_cursor_then_fetch_uses_new_position() -> None:
    """Operator-triggered cursor reset: set the cursor, then the next fetch
    starts from that position. NullConnector ignores the actual position but
    accepts the call without error, which is the contract."""
    connector = NullConnector()
    lea = make_test_lea()
    await connector.authorize_lea(lea.id, AuthParams())

    rewound = Cursor(value="rewound", observed_at=datetime.now(UTC))
    await connector.set_cursor(lea.id, rewound)

    page = await connector.fetch_changes(lea.id, since=rewound)
    assert page.layer_1_check.ok
