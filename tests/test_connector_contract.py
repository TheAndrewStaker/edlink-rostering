"""Contract tests for the Connector protocol.

Every implementation MUST satisfy these assertions. When EdLink lands as the
second concrete connector, it gets parametrized into this fixture and inherits
the same assertions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from edlink_rostering.connectors import Connector, NullConnector
from edlink_rostering.connectors.protocol import AuthParams, EventPage, Layer1Result
from edlink_rostering.core.types import Cursor, LeaId


@pytest.fixture
def connector() -> NullConnector:
    return NullConnector()


def test_connector_satisfies_protocol(connector: NullConnector) -> None:
    """NullConnector is a Connector at runtime.

    runtime_checkable Protocol checks attribute presence, not signatures.
    Signature conformance is enforced by mypy / pyright; this test catches
    accidentally-missing methods at runtime.
    """
    assert isinstance(connector, Connector)
    assert connector.name == "null"


@pytest.mark.asyncio
async def test_authorize_lea_returns_success(connector: NullConnector) -> None:
    lea_id = LeaId("test-lea-001")
    result = await connector.authorize_lea(lea_id, AuthParams())

    assert result.success is True
    assert result.lea_id == lea_id
    assert "read_roster" in result.scopes_granted


@pytest.mark.asyncio
async def test_fetch_changes_returns_event_page(
    connector: NullConnector,
) -> None:
    lea_id = LeaId("test-lea-001")
    cursor = Cursor(value="seed-cursor", observed_at=datetime.now(UTC))

    page = await connector.fetch_changes(lea_id, since=cursor)

    assert isinstance(page, EventPage)
    assert len(page.events) == 1
    assert page.events[0].lea_id == lea_id
    assert page.events[0].source_connector == "null"
    assert page.events[0].event_id
    assert page.next_cursor.value
    assert page.has_more is False
    assert page.layer_1_check.ok is True


@pytest.mark.asyncio
async def test_layer_1_result_carries_http_metadata(
    connector: NullConnector,
) -> None:
    """Layer 1 lives at the connector boundary, not in the sync worker."""
    lea_id = LeaId("test-lea-001")
    cursor = Cursor(value="seed-cursor")

    page = await connector.fetch_changes(lea_id, since=cursor)

    assert isinstance(page.layer_1_check, Layer1Result)
    assert page.layer_1_check.http_status == 200
    assert page.layer_1_check.content_type == "application/json"
    assert page.layer_1_check.body_well_formed is True


@pytest.mark.asyncio
async def test_get_latest_cursor_returns_seed(connector: NullConnector) -> None:
    """Bootstrapping a fresh LEA: the connector tells us where 'now' is."""
    lea_id = LeaId("test-lea-001")
    cursor = await connector.get_latest_cursor(lea_id)

    assert isinstance(cursor, Cursor)
    assert cursor.value


@pytest.mark.asyncio
async def test_set_cursor_round_trip(connector: NullConnector) -> None:
    """Operator CLI / revert path resets the cursor through the connector."""
    lea_id = LeaId("test-lea-001")
    target = Cursor(value="rewound-cursor", observed_at=datetime.now(UTC))

    await connector.set_cursor(lea_id, target)
    # No exception is the contract; persistence is connector-specific.


@pytest.mark.asyncio
async def test_health_returns_status(connector: NullConnector) -> None:
    status = await connector.health()
    assert status.value in {"green", "yellow", "red"}
