"""Fixture-backed HTTP-shaped client for the EdLink Events API (v2.0).

In production this would wrap an httpx.AsyncClient hitting
``GET https://ed.link/api/v2/graph/events?$after=<id>&$first=500`` with a
per-LEA bearer token from Azure Key Vault. The v2.0 contract uses
``$after``, ``$first``, and ``$next`` for pagination (the ``$``-prefixed
OData-style parameters), and each event carries the renamed ``date``
field plus the new ``target``, ``target_id``, ``integration_id``, and
``materialization_id`` fields. In the POC, the client reads from JSON
fixture files at ``fixtures/edlink/<lea_id>.events.json`` so the
connector can be exercised end-to-end without partner credentials.

API version note: EdLink published v2.0 on 2026-05-21 per the verification
sweep in ``docs/partners/edlink-references.md``. Fixture files captured
under the legacy v1.0 shape used ``created_date``; the parser accepts
both ``date`` and ``created_date`` so existing fixtures keep working
while new captures land on the v2.0 envelope.

The response shape mirrors the EdLink v2.0 contract: each event has
``{id, date, type, data, target, target_id, integration_id,
materialization_id}`` where ``type`` follows ``entity.action``
(``person.created``, ``enrollment.updated``, ``person.deleted``, ...).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from edlink_rostering.connectors.protocol import Layer1Result
from edlink_rostering.core.types import LeaId


@dataclass(frozen=True)
class EdLinkEvent:
    """One event from the Events API (v2.0).

    Field shapes match the EdLink v2.0 contract documented at
    https://ed.link/docs/api/v2.0/graph/events. ``data`` is the
    OneRoster 1.2 entity payload for the entity-type implied by ``type``.

    The four ``*_id`` fields and ``target`` / ``target_id`` were added in
    v2.0; legacy fixtures captured against v1.0 omitted them. They
    default to empty strings so events parsed from the older fixture
    shape still construct cleanly.
    """

    id: str
    date: datetime
    type: str
    data: dict[str, Any]
    target: str = ""
    target_id: str = ""
    integration_id: str = ""
    materialization_id: str = ""


@dataclass(frozen=True)
class EdLinkEventsResponse:
    """One page returned by the Events API.

    ``has_more`` is True if additional events exist with IDs greater than the
    highest ID in this page.

    ``next_token`` mirrors the ``$after`` value extracted from EdLink's
    ``$next`` URL in the v2.0 response envelope (per the captured contract
    at https://ed.link/docs/api/v2.0/events/overview and
    ``fixtures/edlink/retention-policy-snapshot.txt``). Callers MUST pass
    it back as the ``$after`` parameter on the next call. EdLink also
    documents the highest event ID in the page as an equivalent cursor
    primitive, so the fixture stand-in returns ``page[-1].id`` here; that
    is what production parsing of ``$next`` would yield against the same
    page. None when the page is empty.

    ``layer_1`` is the connector-boundary response-integrity check. Layer 1
    lives here because it is a check on the response, not on the events.
    """

    events: list[EdLinkEvent]
    has_more: bool
    layer_1: Layer1Result
    next_token: str | None = None


@dataclass(frozen=True)
class EdLinkResourcesResponse:
    """One page of resources returned by the per-resource endpoints.

    Production walks ``GET /api/v2/graph/people``, ``/classes``,
    ``/enrollments``, ``/sections`` with EdLink's v2.0
    ``$after``/``$first``/``$next`` pagination. The POC fixture
    serializes one full page per resource type (no multi-page splits)
    because the canonical scale of the test LEAs keeps the page count
    at one. ``next_token`` is documented here as the production-side
    seam; the fixture client always returns None.
    """

    resources: list[dict[str, Any]]
    has_more: bool
    next_token: str | None
    layer_1: Layer1Result


class EdLinkClient:
    """Process-local Events API client.

    Constructor takes a fixtures directory. Each LEA has its event timeline in
    one JSON file: ``<fixtures_dir>/<lea_id>.events.json``. Format
    (v2.0 envelope; ``created_date`` is accepted as an alias for legacy
    fixtures captured before the v2.0 rename):

        {
          "lea_id": "lea-test-001",
          "events": [
            {"id": "evt_001", "date": "...", "type": "person.created",
             "target": "person", "target_id": "stu-001",
             "integration_id": "...", "materialization_id": "...",
             "data": { ... OneRoster shape ... }},
            ...
          ]
        }

    Events MUST be ordered by id (the API returns them that way; the mock
    relies on the ordering for paging math).
    """

    def __init__(self, fixtures_dir: Path) -> None:
        self._fixtures_dir = fixtures_dir

    def _load_lea_events(self, lea_id: LeaId) -> list[EdLinkEvent]:
        path = self._fixtures_dir / f"{lea_id}.events.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [self._parse_event(evt) for evt in raw.get("events", [])]

    @staticmethod
    def _parse_event(evt: dict[str, Any]) -> EdLinkEvent:
        """Parse one event JSON dict into the dataclass.

        v2.0 wire format puts the event timestamp on ``date``; legacy
        v1.0 fixture files used ``created_date``. Read the v2.0 key
        first and fall back to the v1.0 alias so existing fixtures
        keep working without a rewrite.
        """

        raw_date = evt.get("date", evt.get("created_date"))
        if raw_date is None:
            raise KeyError(
                "EdLink event is missing both 'date' (v2.0) and"
                " 'created_date' (v1.0 alias)."
            )
        return EdLinkEvent(
            id=evt["id"],
            date=datetime.fromisoformat(raw_date),
            type=evt["type"],
            data=evt["data"],
            target=str(evt.get("target", "")),
            target_id=str(evt.get("target_id", "")),
            integration_id=str(evt.get("integration_id", "")),
            materialization_id=str(evt.get("materialization_id", "")),
        )

    async def get_events(
        self, lea_id: LeaId, after: str | None, limit: int
    ) -> EdLinkEventsResponse:
        """Return one page of events with IDs strictly greater than ``after``.

        ``after=None`` (or empty string) means "from the beginning of the
        retention window". Limit caps the page size.
        """

        all_events = self._load_lea_events(lea_id)
        start = self._slice_start(all_events, after)
        page = all_events[start : start + limit]
        has_more = start + limit < len(all_events)
        return EdLinkEventsResponse(
            events=page,
            has_more=has_more,
            layer_1=Layer1Result(
                ok=True,
                http_status=200,
                content_type="application/json",
                body_well_formed=True,
            ),
            next_token=page[-1].id if page else None,
        )

    async def get_latest_event(self, lea_id: LeaId) -> EdLinkEvent | None:
        """Return the most recent event for an LEA, or None if no fixture."""

        all_events = self._load_lea_events(lea_id)
        return all_events[-1] if all_events else None

    async def get_resources(
        self,
        lea_id: LeaId,
        resource_type: str,
        *,
        first: int = 100,
        next_token: str | None = None,
    ) -> EdLinkResourcesResponse:
        """Return one page of resources of ``resource_type`` for an LEA.

        Production: paginated walk of
        ``GET /api/v2/graph/<resource_type>?$first=<n>`` with
        bearer-token auth. When the response has more rows, the body
        carries a ``$next`` URL whose query string includes the
        ``$after`` cursor; callers pass it back as ``next_token``.

        POC: reads ``fixtures/edlink/<lea_id>.resources.json`` under the
        matching top-level key (``people``, ``classes``,
        ``enrollments``, ``academic_sessions``, ``orgs``). The fixture
        is sliced into pages of ``first`` rows each so the walk-loop's
        pagination contract is exercised end-to-end even without a
        live partner. ``next_token`` is the integer page offset
        encoded as a string ("100", "200", ...) so the cursor primitive
        round-trips through JSON cleanly.

        A missing fixture or missing key returns an empty page so the
        reconciliation + bulk-load services can run end-to-end against
        any LEA whose resources fixture has not been authored yet.
        """

        path = self._fixtures_dir / f"{lea_id}.resources.json"
        all_entries: list[dict[str, Any]]
        if not path.exists():
            all_entries = []
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw.get(resource_type)
            all_entries = list(entries) if isinstance(entries, list) else []

        start = int(next_token) if next_token else 0
        page = all_entries[start : start + first]
        end = start + len(page)
        has_more = end < len(all_entries)
        return EdLinkResourcesResponse(
            resources=page,
            has_more=has_more,
            next_token=str(end) if has_more else None,
            layer_1=Layer1Result(
                ok=True,
                http_status=200,
                content_type="application/json",
                body_well_formed=True,
            ),
        )

    def _slice_start(
        self, events: list[EdLinkEvent], after: str | None
    ) -> int:
        if not after:
            return 0
        for i, evt in enumerate(events):
            if evt.id == after:
                return i + 1
        # If the cursor is unknown (cursor past retention, or replay from a
        # deleted event), return end-of-list to surface "no events." The
        # 30-day retention error path is `[VERIFY against sandbox]`; in production
        # this would set cold_start_required on the cursor row.
        return len(events)
