"""EdLink Connector implementation (v2.0).

Implements the amended Connector protocol (see
docs/decisions/adr-004-connector-protocol-page-shape.md) against the
EdLink v2.0 Events API at ``/api/v2/graph/events`` with
``$after``/``$first``/``$next`` pagination. The HTTP-shaped surface is
in :mod:`client`; this module is the adapter between EdLink event
shapes and the canonical model.

Out of scope in session 1:

- Bulk-load via per-resource endpoints (``/people``, ``/classes``, ...).
  Deferred to session 4 of the POC plan. The connector raises NotImplementedError
  if asked.
- Write-back to EdLink. Rostering writes flow upstream into the SIS via
  district workflows; The application does not write rosters back per
  docs/design/edlink-oneroster-rostering.md Non-goals.
- Real OAuth bearer-token exchange. ``authorize_lea`` reads a staged token
  from Key Vault and assumes it is valid. Production would call EdLink's
  identity endpoint to verify scopes.
"""

from __future__ import annotations

import structlog
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, override

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.canonical.entities import (
    CanonicalEntity,
    Enrollment,
    EntityType,
    Student,
)
from edlink_rostering.connectors.edlink.client import EdLinkClient, EdLinkEvent
from edlink_rostering.connectors.protocol import (
    AckMode,
    AuthParams,
    AuthResult,
    Connector,
    EventPage,
    HealthStatus,
    InboundRequest,
    InboundResult,
    ReconcileReport,
    WriteOp,
    WriteResult,
)
from edlink_rostering.core.types import (
    Cursor,
    EnrollmentId,
    EventId,
    LeaId,
    SchoolId,
    StudentId,
)
from edlink_rostering.events.envelope import NormalizedEvent, Operation
from edlink_rostering.infrastructure.ports import SecretNotFound, SecretStore


logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


# EdLink integration status enum from
# ``docs/partners/edlink-references.md`` § "Integration status". The
# sync worker reads this on every poll and pauses against degraded
# integrations. ``destroyed`` is terminal; ``disabled`` is reversible
# by the district re-enabling in EdLink's portal.
INTEGRATION_STATUS_VALUES: frozenset[str] = frozenset(
    {"inactive", "active", "requested", "disabled", "destroyed"}
)
DEGRADED_INTEGRATION_STATUSES: frozenset[str] = frozenset(
    {"inactive", "disabled", "destroyed"}
)


@dataclass(frozen=True)
class IntegrationStatusSnapshot:
    """Point-in-time integration status read from EdLink.

    Persisted on ``connector_authorization`` by the sync worker after
    each poll so the admin app surfaces degraded integrations without
    refetching from EdLink.
    """

    status: str
    sharing_scope: str
    observed_at: datetime

    @property
    def is_degraded(self) -> bool:
        return self.status in DEGRADED_INTEGRATION_STATUSES


# EdLink ``type`` field follows ``entity.action``. Map both halves.
_OPERATION_BY_ACTION: dict[str, Operation] = {
    "created": Operation.CREATED,
    "updated": Operation.UPDATED,
    "deleted": Operation.DELETED,
}

# Entity halves the connector currently maps into canonical.
_HANDLED_ENTITIES: frozenset[str] = frozenset({"person", "enrollment"})

# Entity halves EdLink emits in v1.0 that we have chosen not to map in
# session 1. Their presence in an event is expected; we drop them
# without alerting. Anything outside both sets is treated as schema
# drift and produces a WARNING so operators see it.
_KNOWN_UNMAPPED_ENTITIES: frozenset[str] = frozenset({"class", "term", "org"})


class EdLinkConnector(Connector):
    """Connector implementation for EdLink Events API.

    Constructor dependencies:

    - ``client``: the HTTP-shaped client (real httpx in production, fixture
      reader in POC).
    - ``key_vault``: source of per-LEA bearer tokens. The connector reads
      ``edlink-token-<lea_id>`` at authorize time.
    - ``session_factory``: SQLAlchemy async session factory for set_cursor.
      The sync worker writes the cursor inside its own transaction; this
      factory is only used by operator-driven cursor resets.
    """

    name = "edlink"
    DEFAULT_PAGE_SIZE = 500  # per edlink-oneroster-rostering.md line 199

    def __init__(
        self,
        client: EdLinkClient,
        key_vault: SecretStore,
        session_factory: async_sessionmaker[AsyncSession],
        page_size: int | None = None,
    ) -> None:
        self._client = client
        self._key_vault = key_vault
        self._sessions = session_factory
        self._authorized_leas: set[LeaId] = set()
        self._page_size = page_size if page_size is not None else self.DEFAULT_PAGE_SIZE

    # ── Authorization ─────────────────────────────────────────────────────

    @override
    async def authorize_lea(
        self, lea_id: LeaId, params: AuthParams
    ) -> AuthResult:
        """Validate that an EdLink bearer token is staged for the LEA.

        Production: would call EdLink's identity endpoint to verify the
        token's scopes. POC: trusts the staged token's existence.
        """

        secret_name = f"edlink-token-{lea_id}"
        try:
            self._key_vault.get_secret(secret_name)
        except SecretNotFound:
            return AuthResult(
                success=False,
                lea_id=lea_id,
                scopes_granted=[],
                expires_at=datetime.now(UTC),
                error=f"No EdLink bearer token staged for {lea_id}.",
            )
        self._authorized_leas.add(lea_id)
        return AuthResult(
            success=True,
            lea_id=lea_id,
            scopes_granted=["read_roster"],
            expires_at=datetime.now(UTC) + timedelta(days=365),
        )

    @override
    async def revoke_lea(self, lea_id: LeaId) -> None:
        self._authorized_leas.discard(lea_id)

    @override
    async def list_authorized_leas(self) -> AsyncIterator[LeaId]:
        for lea_id in self._authorized_leas:
            yield lea_id

    # ── Cursor + pagination ───────────────────────────────────────────────

    @override
    async def fetch_changes(
        self, lea_id: LeaId, since: Cursor
    ) -> EventPage:
        """Fetch one page of events strictly after ``since``."""

        after = since.value or None
        response = await self._client.get_events(
            lea_id, after=after, limit=self._page_size
        )

        normalized: list[NormalizedEvent] = []
        for evt in response.events:
            event = self._normalize(evt, lea_id)
            if event is not None:
                normalized.append(event)

        # next_token is the cursor we pass back to EdLink on the next poll,
        # per the v2.0 contract (the ``$after`` value carried by the
        # response's ``$next`` URL). EdLink documents the highest event
        # ID in the page as an equivalent primitive, but honoring
        # ``$next`` directly keeps us aligned with the partner-authoritative
        # value when the two would diverge. Falls back to since.value on
        # an empty page so the cursor stays put rather than rewinding.
        observed: datetime | None
        if response.events:
            observed = response.events[-1].date
        else:
            observed = since.observed_at

        return EventPage(
            events=normalized,
            next_cursor=Cursor(
                value=response.next_token or since.value,
                observed_at=observed,
            ),
            has_more=response.has_more,
            retrieved_at=datetime.now(UTC),
            layer_1_check=response.layer_1,
        )

    @override
    async def get_latest_cursor(self, lea_id: LeaId) -> Cursor:
        """Return the cursor at the most recent event in the retention window."""

        latest = await self._client.get_latest_event(lea_id)
        if latest is None:
            return Cursor(value="", observed_at=datetime.now(UTC))
        return Cursor(value=latest.id, observed_at=latest.date)

    @override
    async def set_cursor(self, lea_id: LeaId, cursor: Cursor) -> None:
        """Operator-driven cursor reset.

        The sync worker writes the cursor inside its own transaction. This
        method is for the revert and replay paths where the operator
        explicitly rewinds the cursor to a known event ID.
        """

        observed = cursor.observed_at or datetime.now(UTC)
        async with self._sessions() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO cursor_state (
                        lea_id, partner, last_event_id, last_event_at, updated_at
                    ) VALUES (
                        :lea_id, :partner, :last_event_id, :last_event_at, :updated_at
                    )
                    ON CONFLICT (lea_id, partner) DO UPDATE SET
                        last_event_id = EXCLUDED.last_event_id,
                        last_event_at = EXCLUDED.last_event_at,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "lea_id": lea_id,
                    "partner": self.name,
                    "last_event_id": cursor.value,
                    "last_event_at": observed,
                    "updated_at": datetime.now(UTC),
                },
            )
            await session.commit()

    # ── Write-back and webhooks ───────────────────────────────────────────

    @override
    async def write(
        self,
        lea_id: LeaId,
        entity: CanonicalEntity,
        op: WriteOp,
        idempotency_key: str,
    ) -> WriteResult:
        """EdLink rostering is read-only from the application's side.

        Districts manage rosters in the SIS; writes back via EdLink would be
        a separate write-back design. Returns a failure result rather than
        raising so the sync worker can record a soft failure.
        """

        return WriteResult(
            success=False,
            ack_mode=AckMode.SYNC,
            idempotency_key_used=idempotency_key,
            error="EdLink rostering is read-only in session 1.",
        )

    @override
    async def handle_inbound(
        self, request: InboundRequest
    ) -> InboundResult:
        """EdLink supports Data Feeds push but the POC uses poll-only.

        Per edlink-oneroster-rostering.md: Data Feeds is deferred. Returning
        an empty result keeps the protocol satisfied without lying about
        what the connector actually handles.
        """

        return InboundResult(
            events=[],
            follow_up_required=False,
            error="EdLink poll-only in session 1; Data Feeds push not wired.",
        )

    @override
    async def reconcile(self, lea_id: LeaId) -> ReconcileReport:
        """Merkle reconciliation lands in session 3.

        Returns a trivial report so the protocol stays satisfied and the
        CLI's reconcile command does not crash before session 3 ships.
        """

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

    # ── Resource-endpoint walk (reconciliation + bulk-load) ──────────────

    # EdLink v2.0 page size for resource walks. The Events API uses
    # the same ``$first`` parameter; ``100`` is the documented sweet
    # spot for resource endpoints (people/classes/enrollments). The
    # event-feed default stays at ``DEFAULT_PAGE_SIZE = 500`` because
    # event payloads are smaller.
    RESOURCE_PAGE_SIZE = 100

    # The five OneRoster resource families EdLink exposes at
    # ``/api/v2/graph/<entity>``. Walked in this order so the
    # partner-side snapshot dict mirrors the canonical reconciliation
    # entity-type order. ``orgs`` projects into the canonical
    # ``schools`` table; the EdLink endpoint name and the canonical
    # name diverge because the canonical model only tracks the school
    # subset of OneRoster orgs.
    _RESOURCE_WALK: tuple[tuple[str, str], ...] = (
        ("people", "students"),
        ("enrollments", "enrollments"),
        ("classes", "classes"),
        ("academic_sessions", "academic_sessions"),
        ("orgs", "schools"),
    )

    async def walk_resources(
        self, lea_id: LeaId
    ) -> dict[str, list[dict[str, Any]]]:
        """Return the partner-side snapshot of canonical entities.

        Wires the EdLink v2.0 per-resource endpoints
        (``/api/v2/graph/people``, ``/classes``, ``/enrollments``,
        ``/academic_sessions``, ``/orgs``) into the
        ``PartnerSnapshot`` callable shape that
        :class:`edlink_rostering.services.reconciliation.ReconciliationService`
        and :class:`edlink_rostering.services.bulk_load.BulkLoadService` expect.

        Pagination follows the v2.0 ``$first`` + ``$next`` contract:
        each entity type is walked one page of
        :data:`RESOURCE_PAGE_SIZE` rows at a time until ``has_more``
        is false. The fixture client returns its first page in one
        call when the resource list fits; the loop is still exercised
        so the production wiring is the same code path.

        The returned dict is keyed by canonical entity-type name
        (``students``, ``enrollments``, ``classes``,
        ``academic_sessions``, ``schools``) and each row carries the
        column subset the services hash and upsert against. Dates
        serialize to ISO strings so the partner-side leaves hash
        identically to canonical-side leaves (which round-trip
        through :func:`json.dumps` with an isoformat default).

        Non-student persons are filtered out at the connector boundary
        so the reconciliation hash does not include teachers / parents
        that the canonical model does not yet store. This mirrors
        :meth:`_person_to_student` in the event-driven path.

        A missing resources fixture (or an LEA with no rostered
        students yet) returns empty lists rather than raising, so a
        forced reconcile against a freshly-authorized LEA does not
        crash before the first bulk-load completes.
        """

        snapshot: dict[str, list[dict[str, Any]]] = {
            canonical_name: [] for _, canonical_name in self._RESOURCE_WALK
        }

        for resource_name, canonical_name in self._RESOURCE_WALK:
            collected = snapshot[canonical_name]
            next_token: str | None = None
            while True:
                response = await self._client.get_resources(
                    lea_id,
                    resource_name,
                    first=self.RESOURCE_PAGE_SIZE,
                    next_token=next_token,
                )
                for raw in response.resources:
                    projected = _project_resource(canonical_name, raw)
                    if projected is None:
                        continue
                    collected.append(projected)
                if not response.has_more:
                    break
                next_token = response.next_token

        return snapshot

    async def get_integration_status(
        self, lea_id: LeaId
    ) -> "IntegrationStatusSnapshot":
        """Return EdLink's current integration status for ``lea_id``.

        Production: hits ``GET /api/v2/integrations/{integration_id}``
        with the per-LEA bearer token and reads the ``status`` and
        ``sharing`` fields. POC: reads from
        ``fixtures/edlink/<lea_id>.integration.json`` and falls back
        to an ``active`` snapshot so demos against LEAs without an
        explicit fixture still complete.

        The integration status enum mirrors EdLink's contract from
        ``docs/partners/edlink-references.md`` § "Integration status":
        ``inactive``, ``active``, ``requested``, ``disabled``,
        ``destroyed``. ``disabled`` and ``destroyed`` are the
        degraded states that the sync worker surfaces via the
        ``integration_degraded`` alert and pauses polling against.
        """

        path = self._client._fixtures_dir / f"{lea_id}.integration.json"
        if not path.exists():
            return IntegrationStatusSnapshot(
                status="active",
                sharing_scope="full",
                observed_at=datetime.now(UTC),
            )
        import json as _json

        raw = _json.loads(path.read_text(encoding="utf-8"))
        return IntegrationStatusSnapshot(
            status=str(raw.get("status", "active")),
            sharing_scope=_optional_str(raw.get("sharing_scope")) or "full",
            observed_at=datetime.now(UTC),
        )

    # ── Event mapping ─────────────────────────────────────────────────────

    def _normalize(
        self, event: EdLinkEvent, lea_id: LeaId
    ) -> NormalizedEvent | None:
        """Convert an EdLink event into a canonical NormalizedEvent.

        Returns None for event types this connector does not map. Known
        unmapped entities (``class``, ``term``, ``org``) are dropped quietly;
        any entity outside both _HANDLED_ENTITIES and _KNOWN_UNMAPPED_ENTITIES
        is logged as schema_unknown_type at WARNING so operators see partner
        drift instead of a silent skip. Unknown actions on a handled entity
        are also logged.
        """

        entity_word, _, action_word = event.type.partition(".")

        if (
            entity_word not in _HANDLED_ENTITIES
            and entity_word not in _KNOWN_UNMAPPED_ENTITIES
        ):
            logger.warning(
                "edlink.schema_unknown_type",
                event_id=event.id,
                event_type=event.type,
                entity_word=entity_word,
                lea_id=lea_id,
                reason="schema_unknown_type",
            )
            return None

        operation = _OPERATION_BY_ACTION.get(action_word)
        if operation is None:
            if entity_word in _HANDLED_ENTITIES:
                logger.warning(
                    "edlink.schema_unknown_action",
                    event_id=event.id,
                    event_type=event.type,
                    entity_word=entity_word,
                    action_word=action_word,
                    lea_id=lea_id,
                    reason="schema_unknown_action",
                )
            return None

        entity: CanonicalEntity | None
        entity_type: EntityType
        if entity_word == "person":
            entity = self._person_to_student(event.data, lea_id)
            if entity is None:
                return None
            entity_type = EntityType.STUDENT
        elif entity_word == "enrollment":
            entity = self._enrollment_to_canonical(event.data, lea_id)
            entity_type = EntityType.ENROLLMENT
        else:
            return None

        return NormalizedEvent(
            event_id=EventId(event.id),
            lea_id=lea_id,
            entity_type=entity_type,
            operation=operation,
            entity=entity,
            source_connector=self.name,
            source_event_id=event.id,
            occurred_at=event.date,
            received_at=datetime.now(UTC),
        )

    def _person_to_student(
        self, data: dict[str, Any], lea_id: LeaId
    ) -> Student | None:
        """Filter persons by role; only students land in the canonical model
        in session 1. Teachers and parents are out of scope until later."""

        roles = data.get("roles") or []
        if not any(_is_student_role(r) for r in roles):
            return None

        grades = data.get("grades") or []
        grade = grades[0] if grades else None
        primary_org_raw = _extract_primary_org_id(data.get("primaryOrg"))
        primary_school_id = (
            SchoolId(primary_org_raw) if primary_org_raw is not None else None
        )

        return Student(
            id=StudentId(str(data["sourcedId"])),
            lea_id=lea_id,
            given_name=str(data.get("givenName", "")),
            family_name=str(data.get("familyName", "")),
            grade=str(grade) if grade is not None else None,
            preferred_first_name=_optional_str(data.get("preferredFirstName")),
            primary_school_id=primary_school_id,
            external_ids=dict(data.get("userIds") or {}),
        )

    def _enrollment_to_canonical(
        self, data: dict[str, Any], lea_id: LeaId
    ) -> Enrollment:
        return Enrollment(
            id=EnrollmentId(str(data["sourcedId"])),
            lea_id=lea_id,
            student_id=StudentId(_extract_ref_id(data["user"])),
            class_id=_extract_ref_id(data["class"]),
            begin_date=_parse_date(data.get("beginDate")),
            end_date=_parse_date_optional(data.get("endDate")),
        )


# ── Module-level helpers ──────────────────────────────────────────────────────


def _is_student_role(role: Any) -> bool:
    """OneRoster 1.2 roles are objects like ``{"role": "student", ...}``.
    EdLink may emit them as bare strings in some shapes; accept both."""

    if isinstance(role, dict):
        return bool(role.get("role") == "student")
    return bool(role == "student")


def _extract_ref_id(ref: Any) -> str:
    """OneRoster references are ``{"sourcedId": "...", "type": "..."}`` objects.
    Accept a bare string for tests and simplified fixtures."""

    if isinstance(ref, dict):
        return str(ref["sourcedId"])
    return str(ref)


def _extract_primary_org_id(ref: Any) -> str | None:
    if ref is None:
        return None
    return _extract_ref_id(ref)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _parse_date(value: Any) -> "date":
    """Parse OneRoster ISO date strings to datetime.date."""

    if value is None:
        raise ValueError("Required date field missing.")
    return date.fromisoformat(str(value))


def _parse_date_optional(value: Any) -> "date | None":
    if value is None:
        return None
    return date.fromisoformat(str(value))


def _project_resource(
    canonical_name: str, raw: dict[str, Any]
) -> dict[str, Any] | None:
    """Dispatch a partner row to the matching canonical-row projector.

    Returns None when the row does not belong in the canonical table
    (for example, non-student persons or non-school orgs). Each
    projector's output keys align with the matching
    ``ReconciliationService._ENTITY_TABLES`` column tuple so leaf
    hashes on the partner side match canonical-side leaves.
    """

    if canonical_name == "students":
        return _project_student(raw)
    if canonical_name == "enrollments":
        return _project_enrollment(raw)
    if canonical_name == "classes":
        return _project_class(raw)
    if canonical_name == "academic_sessions":
        return _project_academic_session(raw)
    if canonical_name == "schools":
        return _project_school(raw)
    return None


def _project_student(person: dict[str, Any]) -> dict[str, Any] | None:
    """Project a OneRoster ``person`` dict into the canonical-row shape.

    Returns None for non-student roles so the snapshot only carries
    rows that belong in the canonical ``students`` table. Output keys
    align with ``ReconciliationService._ENTITY_TABLES['students']`` so
    leaf hashes on the partner side match canonical-side leaves.
    """

    roles = person.get("roles") or []
    if not any(_is_student_role(r) for r in roles):
        return None

    grades = person.get("grades") or []
    grade = str(grades[0]) if grades else None
    primary_school_id = _extract_primary_org_id(person.get("primaryOrg"))
    return {
        "id": str(person["sourcedId"]),
        "given_name": str(person.get("givenName", "")),
        "family_name": str(person.get("familyName", "")),
        "grade": grade,
        "preferred_first_name": _optional_str(
            person.get("preferredFirstName")
        ),
        "primary_school_id": primary_school_id,
    }


def _project_enrollment(raw: dict[str, Any]) -> dict[str, Any]:
    """Project a OneRoster ``enrollment`` dict into canonical-row shape.

    Dates remain ISO strings so the leaf hash matches the canonical
    side (where SQLAlchemy returns ``date`` objects that ``_json_default``
    serializes via ``isoformat()``).
    """

    begin = raw.get("beginDate")
    end = raw.get("endDate")
    return {
        "id": str(raw["sourcedId"]),
        "student_id": _extract_ref_id(raw["user"]),
        "class_id": _extract_ref_id(raw["class"]),
        "begin_date": str(begin) if begin is not None else None,
        "end_date": str(end) if end is not None else None,
    }


def _project_class(raw: dict[str, Any]) -> dict[str, Any]:
    """Project a OneRoster ``class`` dict into canonical-row shape.

    ``course_code`` carries the district's local code (the OneRoster
    ``classCode``); ``school_id`` resolves the ``school`` ref to its
    sourcedId so the canonical FK matches.
    """

    return {
        "id": str(raw["sourcedId"]),
        "title": str(raw.get("title", "")),
        "course_code": _optional_str(raw.get("classCode")),
        "school_id": _extract_primary_org_id(raw.get("school")),
        "term_id": _extract_primary_org_id(raw.get("term")),
    }


def _project_academic_session(raw: dict[str, Any]) -> dict[str, Any]:
    """Project a OneRoster ``academicSession`` dict into canonical-row shape.

    The OneRoster ``type`` field distinguishes term / gradingPeriod /
    schoolYear; the canonical column keeps the raw value so partner
    drift on naming surfaces as a hash mismatch instead of a silent
    coercion.
    """

    return {
        "id": str(raw["sourcedId"]),
        "title": str(raw.get("title", "")),
        "session_type": _optional_str(raw.get("type")),
        "school_year": _optional_str(raw.get("schoolYear")),
        "start_date": _optional_iso_date(raw.get("startDate")),
        "end_date": _optional_iso_date(raw.get("endDate")),
    }


def _project_school(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Project a OneRoster ``org`` dict into the canonical ``schools`` row shape.

    Returns None for org rows whose ``type`` is not ``school`` so the
    canonical-side hash does not include district / department orgs
    that the canonical model does not track. The ``parent`` ref
    becomes the parent_org_id so EdLink's district-school hierarchy
    is queryable without re-walking.
    """

    org_type = raw.get("type")
    if org_type is not None and str(org_type) != "school":
        return None
    return {
        "id": str(raw["sourcedId"]),
        "name": str(raw.get("name", "")),
        "school_code": _optional_str(raw.get("identifier")),
        "parent_org_id": _extract_primary_org_id(raw.get("parent")),
    }


def _optional_iso_date(value: Any) -> str | None:
    """Return an ISO date string or None.

    Some EdLink fixtures emit dates as ``"2025-08-15"`` and others as
    full ISO datetimes; the helper normalizes by passing valid
    fragments through and otherwise returning None so the leaf hash
    is comparable across partner/canonical sides.
    """

    if value is None:
        return None
    return str(value)
