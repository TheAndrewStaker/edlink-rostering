"""Tests for the fixture-backed EdLink Events API client.

These tests have no DB or network dependency; they exercise the fixture
loader and the paging arithmetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edlink_rostering.connectors.edlink import EdLinkClient
from edlink_rostering.core.types import LeaId


@pytest.fixture
def client(edlink_fixtures_dir: Path) -> EdLinkClient:
    return EdLinkClient(fixtures_dir=edlink_fixtures_dir)


@pytest.mark.asyncio
async def test_get_events_first_page_from_beginning(client: EdLinkClient) -> None:
    response = await client.get_events(LeaId("lea-test-001"), after=None, limit=3)

    assert response.layer_1.ok is True
    assert response.layer_1.http_status == 200
    assert [e.id for e in response.events] == ["evt_001", "evt_002", "evt_003"]
    assert response.has_more is True


@pytest.mark.asyncio
async def test_get_events_paginates_with_after(client: EdLinkClient) -> None:
    page1 = await client.get_events(LeaId("lea-test-001"), after=None, limit=3)
    page2 = await client.get_events(
        LeaId("lea-test-001"), after=page1.events[-1].id, limit=3
    )

    assert [e.id for e in page2.events] == ["evt_004", "evt_005", "evt_006"]
    assert page2.has_more is True


@pytest.mark.asyncio
async def test_get_events_returns_remainder_and_marks_done(
    client: EdLinkClient,
) -> None:
    page = await client.get_events(LeaId("lea-test-001"), after="evt_006", limit=10)

    assert [e.id for e in page.events] == ["evt_007", "evt_008"]
    assert page.has_more is False


@pytest.mark.asyncio
async def test_get_events_at_end_returns_empty(client: EdLinkClient) -> None:
    page = await client.get_events(LeaId("lea-test-001"), after="evt_008", limit=10)

    assert page.events == []
    assert page.has_more is False


@pytest.mark.asyncio
async def test_get_events_unknown_lea_returns_empty(client: EdLinkClient) -> None:
    page = await client.get_events(LeaId("lea-not-in-fixtures"), after=None, limit=10)

    assert page.events == []
    assert page.has_more is False


@pytest.mark.asyncio
async def test_get_latest_event_returns_highest(client: EdLinkClient) -> None:
    latest = await client.get_latest_event(LeaId("lea-test-001"))

    assert latest is not None
    assert latest.id == "evt_008"
    assert latest.type == "person.deleted"


@pytest.mark.asyncio
async def test_get_latest_event_unknown_lea_returns_none(
    client: EdLinkClient,
) -> None:
    latest = await client.get_latest_event(LeaId("lea-not-in-fixtures"))
    assert latest is None
