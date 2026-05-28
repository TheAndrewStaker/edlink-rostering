"""Tests for the EdLink Connector implementation.

Covers:

- Authorization against the Key Vault mock.
- Event-shape mapping from EdLink payloads to NormalizedEvent + canonical
  entities. The mapping is the most failure-prone bit of the connector and
  the part that would silently rot if EdLink's payload shape drifted.
- Cursor bootstrap (get_latest_cursor) and paged fetch_changes.
- Operator-driven set_cursor against a real Postgres (skipped if no DB).

DB-free tests use an in-memory KeyVault mock and a "factory" placeholder
that is never invoked. The DB-bound test uses the db_session_factory fixture
from conftest.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from edlink_rostering.canonical.entities import Enrollment, EntityType, Student
from edlink_rostering.connectors import Connector
from edlink_rostering.connectors.edlink import EdLinkClient, EdLinkConnector
from edlink_rostering.connectors.protocol import AuthParams, EventPage
from edlink_rostering.core.types import Cursor, LeaId
from edlink_rostering.events.envelope import Operation
from edlink_rostering.infrastructure.azure_mocks import KeyVaultClient


@pytest.fixture
def key_vault() -> KeyVaultClient:
    vault = KeyVaultClient()
    vault.put_secret("edlink-token-lea-test-001", "bearer-fake-001")
    return vault


@pytest.fixture
def session_factory_placeholder() -> Any:
    """Placeholder session factory for tests that never call set_cursor.

    Raises if invoked, so tests that accidentally trigger DB writes fail
    loudly instead of silently."""

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "This test should not write to the database; use db_session_factory"
            " if you need a real session."
        )

    return _raise


@pytest.fixture
def connector(
    edlink_fixtures_dir: Path,
    key_vault: KeyVaultClient,
    session_factory_placeholder: Any,
) -> EdLinkConnector:
    client = EdLinkClient(fixtures_dir=edlink_fixtures_dir)
    return EdLinkConnector(
        client=client,
        key_vault=key_vault,
        session_factory=session_factory_placeholder,
    )


# ── Protocol conformance ──────────────────────────────────────────────────────


def test_satisfies_connector_protocol(connector: EdLinkConnector) -> None:
    assert isinstance(connector, Connector)
    assert connector.name == "edlink"


# ── Authorization ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authorize_lea_succeeds_when_token_staged(
    connector: EdLinkConnector,
) -> None:
    result = await connector.authorize_lea(LeaId("lea-test-001"), AuthParams())

    assert result.success is True
    assert result.lea_id == "lea-test-001"
    assert "read_roster" in result.scopes_granted
    authorized = [lid async for lid in connector.list_authorized_leas()]
    assert "lea-test-001" in authorized


@pytest.mark.asyncio
async def test_authorize_lea_fails_when_no_token(
    connector: EdLinkConnector,
) -> None:
    result = await connector.authorize_lea(LeaId("lea-unknown"), AuthParams())

    assert result.success is False
    assert result.error is not None
    assert "No EdLink bearer token" in result.error


@pytest.mark.asyncio
async def test_revoke_lea_removes_from_authorized_list(
    connector: EdLinkConnector,
) -> None:
    await connector.authorize_lea(LeaId("lea-test-001"), AuthParams())
    await connector.revoke_lea(LeaId("lea-test-001"))

    authorized = [lid async for lid in connector.list_authorized_leas()]
    assert "lea-test-001" not in authorized


# ── Fetch + mapping ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_changes_returns_event_page(
    connector: EdLinkConnector,
) -> None:
    cursor = Cursor(value="", observed_at=datetime.now())
    page = await connector.fetch_changes(LeaId("lea-test-001"), since=cursor)

    assert isinstance(page, EventPage)
    assert page.layer_1_check.ok is True
    assert page.next_cursor.value == "evt_008"
    assert page.has_more is False
    assert len(page.events) == 8


@pytest.mark.asyncio
async def test_person_created_maps_to_student_normalized_event(
    connector: EdLinkConnector,
) -> None:
    cursor = Cursor(value="")
    page = await connector.fetch_changes(LeaId("lea-test-001"), since=cursor)
    person_created = page.events[0]

    assert person_created.event_id == "evt_001"
    assert person_created.lea_id == "lea-test-001"
    assert person_created.entity_type == EntityType.STUDENT
    assert person_created.operation == Operation.CREATED
    assert person_created.source_connector == "edlink"
    assert isinstance(person_created.entity, Student)

    student = person_created.entity
    assert student.id == "stu-001"
    assert student.given_name == "Alex"
    assert student.family_name == "Morgan"
    assert student.grade == "05"
    assert student.primary_school_id == "sch-001"
    assert student.external_ids == {"sis": "100001", "edlink": "edl-100001"}


@pytest.mark.asyncio
async def test_enrollment_created_maps_to_enrollment_normalized_event(
    connector: EdLinkConnector,
) -> None:
    cursor = Cursor(value="evt_003")
    page = await connector.fetch_changes(LeaId("lea-test-001"), since=cursor)
    enrollment_created = page.events[0]

    assert enrollment_created.entity_type == EntityType.ENROLLMENT
    assert enrollment_created.operation == Operation.CREATED
    assert isinstance(enrollment_created.entity, Enrollment)

    enrollment = enrollment_created.entity
    assert enrollment.id == "enr-001"
    assert enrollment.student_id == "stu-001"
    assert enrollment.class_id == "cls-A"


@pytest.mark.asyncio
async def test_person_deleted_maps_to_delete_operation(
    connector: EdLinkConnector,
) -> None:
    cursor = Cursor(value="evt_007")
    page = await connector.fetch_changes(LeaId("lea-test-001"), since=cursor)
    deletion = page.events[0]

    assert deletion.event_id == "evt_008"
    assert deletion.operation == Operation.DELETED
    assert deletion.entity_type == EntityType.STUDENT


@pytest.mark.asyncio
async def test_unknown_entity_type_logs_schema_unknown_type(
    connector: EdLinkConnector,
) -> None:
    """Unknown entity halves of the EdLink ``entity.action`` type must
    produce a WARNING-level log line tagged schema_unknown_type so partner
    drift is visible to operators, not silently dropped.

    Uses ``structlog.testing.capture_logs`` since the connector emits
    via structlog now; the previous ``caplog`` shape captured stdlib
    LogRecords which no longer carry the structured kwargs as
    attributes.
    """

    from structlog.testing import capture_logs

    from edlink_rostering.connectors.edlink.client import EdLinkEvent

    event = EdLinkEvent(
        id="evt_xyz",
        date=datetime(2026, 5, 21),
        type="parent.created",
        data={"id": "par-001"},
    )

    with capture_logs() as logs:
        result = connector._normalize(event, LeaId("lea-test-001"))

    assert result is None
    matching = [r for r in logs if r["event"] == "edlink.schema_unknown_type"]
    assert len(matching) == 1
    assert matching[0]["reason"] == "schema_unknown_type"
    assert matching[0]["entity_word"] == "parent"


@pytest.mark.asyncio
async def test_known_unmapped_entity_does_not_warn(
    connector: EdLinkConnector,
) -> None:
    """Entities we intentionally do not map yet (``class``, ``term``,
    ``org``) drop without logging. Their presence is expected v1.0 EdLink
    behavior and not a drift signal."""

    from structlog.testing import capture_logs

    from edlink_rostering.connectors.edlink.client import EdLinkEvent

    event = EdLinkEvent(
        id="evt_term_001",
        date=datetime(2026, 5, 21),
        type="term.created",
        data={"id": "trm-001"},
    )

    with capture_logs() as logs:
        result = connector._normalize(event, LeaId("lea-test-001"))

    assert result is None
    assert not any(r["event"] == "edlink.schema_unknown_type" for r in logs)


@pytest.mark.asyncio
async def test_unknown_action_on_handled_entity_logs_schema_unknown_action(
    connector: EdLinkConnector,
) -> None:
    """A handled entity with an unrecognized action (for example
    ``person.archived``) produces a WARNING tagged schema_unknown_action
    so the operator sees the partner contract drift."""

    from structlog.testing import capture_logs

    from edlink_rostering.connectors.edlink.client import EdLinkEvent

    event = EdLinkEvent(
        id="evt_arch_001",
        date=datetime(2026, 5, 21),
        type="person.archived",
        data={"id": "stu-001"},
    )

    with capture_logs() as logs:
        result = connector._normalize(event, LeaId("lea-test-001"))

    assert result is None
    matching = [r for r in logs if r["event"] == "edlink.schema_unknown_action"]
    assert len(matching) == 1
    assert matching[0]["action_word"] == "archived"


@pytest.mark.asyncio
async def test_fetch_changes_paginates(connector: EdLinkConnector) -> None:
    """Connector default page size is 500, larger than the fixture, so the
    paging test substitutes a smaller limit by going through the client
    directly. Here we verify the connector advances the cursor correctly
    when called twice in a row."""

    page1 = await connector.fetch_changes(
        LeaId("lea-test-001"), since=Cursor(value="")
    )
    page2 = await connector.fetch_changes(
        LeaId("lea-test-001"), since=page1.next_cursor
    )

    assert page1.has_more is False
    assert page2.events == []
    assert page2.next_cursor.value == page1.next_cursor.value


@pytest.mark.asyncio
async def test_cursor_uses_next_token_not_highest_event_id(
    key_vault: KeyVaultClient,
    session_factory_placeholder: Any,
) -> None:
    """Production EdLink returns a ``$next`` URL in the response envelope.
    The connector must use the ``$after`` value EdLink advertises there,
    not infer one from the page contents.

    EdLink documents the two as equivalent on the happy path (per the
    captured contract in ``fixtures/edlink/retention-policy-snapshot.txt``),
    but the partner-authoritative value is ``$next``. This test pins the
    behavior by injecting a next_token that is deliberately not equal to
    any event id in the page; the connector must propagate it verbatim
    onto ``next_cursor.value``.
    """

    from edlink_rostering.connectors.edlink.client import (
        EdLinkEvent,
        EdLinkEventsResponse,
    )
    from edlink_rostering.connectors.protocol import Layer1Result

    class FakeClient:
        async def get_events(
            self, lea_id: LeaId, after: str | None, limit: int
        ) -> EdLinkEventsResponse:
            return EdLinkEventsResponse(
                events=[
                    EdLinkEvent(
                        id="evt_001",
                        date=datetime(2026, 5, 21),
                        type="person.created",
                        data={
                            "sourcedId": "stu-001",
                            "roles": [{"role": "student"}],
                            "givenName": "A",
                            "familyName": "B",
                        },
                    )
                ],
                has_more=True,
                layer_1=Layer1Result(
                    ok=True,
                    http_status=200,
                    content_type="application/json",
                    body_well_formed=True,
                ),
                next_token="opaque_pagination_token_xyz",
            )

    connector = EdLinkConnector(
        client=FakeClient(),  # type: ignore[arg-type]
        key_vault=key_vault,
        session_factory=session_factory_placeholder,
    )
    page = await connector.fetch_changes(
        LeaId("lea-test-001"), since=Cursor(value="", observed_at=None)
    )

    assert page.next_cursor.value == "opaque_pagination_token_xyz"


# ── Cursor bootstrap ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_latest_cursor_returns_highest_event(
    connector: EdLinkConnector,
) -> None:
    cursor = await connector.get_latest_cursor(LeaId("lea-test-001"))
    assert cursor.value == "evt_008"


@pytest.mark.asyncio
async def test_get_latest_cursor_unknown_lea_returns_empty(
    connector: EdLinkConnector,
) -> None:
    cursor = await connector.get_latest_cursor(LeaId("lea-not-in-fixtures"))
    assert cursor.value == ""


# ── walk_resources (production seam for reconciliation + bulk-load) ──────────


@pytest.mark.asyncio
async def test_walk_resources_projects_students_and_filters_non_students(
    connector: EdLinkConnector,
) -> None:
    """``walk_resources`` returns canonical-shaped student rows.

    The fixture seeds two students and one teacher; the teacher is
    filtered at the connector boundary so the canonical-side hash
    does not need to know about non-student persons.
    """

    snapshot = await connector.walk_resources(LeaId("lea-test-001"))

    students = snapshot["students"]
    assert {row["id"] for row in students} == {"stu-001", "stu-002"}

    by_id = {row["id"]: row for row in students}
    alex = by_id["stu-001"]
    assert alex["given_name"] == "Alex"
    assert alex["family_name"] == "Morgan"
    assert alex["grade"] == "06"
    assert alex["preferred_first_name"] is None
    assert alex["primary_school_id"] == "sch-001"

    bryn = by_id["stu-002"]
    assert bryn["grade"] == "06"
    assert bryn["preferred_first_name"] == "Brynn"


@pytest.mark.asyncio
async def test_walk_resources_projects_enrollments_with_iso_dates(
    connector: EdLinkConnector,
) -> None:
    """Enrollment rows carry unwrapped ref ids and ISO date strings.

    Dates stay strings so the partner-side leaf hash aligns with the
    canonical-side leaf (canonical dates round-trip through
    ``json.dumps`` with an ``isoformat`` default).
    """

    snapshot = await connector.walk_resources(LeaId("lea-test-001"))

    enrollments = snapshot["enrollments"]
    assert {row["id"] for row in enrollments} == {"enr-001", "enr-002"}
    enr_001 = next(r for r in enrollments if r["id"] == "enr-001")
    assert enr_001["student_id"] == "stu-001"
    assert enr_001["class_id"] == "cls-A"
    assert enr_001["begin_date"] == "2026-08-15"
    assert enr_001["end_date"] == "2027-06-12"


@pytest.mark.asyncio
async def test_walk_resources_projects_classes(
    connector: EdLinkConnector,
) -> None:
    """``classes`` rows resolve school + term refs to sourcedIds and
    carry the partner-side classCode as the canonical course_code."""

    snapshot = await connector.walk_resources(LeaId("lea-test-001"))

    classes = snapshot["classes"]
    assert {row["id"] for row in classes} == {"cls-A"}
    cls_a = next(r for r in classes if r["id"] == "cls-A")
    assert cls_a["title"] == "Sixth Grade Homeroom A"
    assert cls_a["course_code"] == "6-HR-A"
    assert cls_a["school_id"] == "sch-001"
    assert cls_a["term_id"] == "term-2026-fall"


@pytest.mark.asyncio
async def test_walk_resources_projects_academic_sessions(
    connector: EdLinkConnector,
) -> None:
    """Academic sessions preserve OneRoster ``type`` so a term/grading
    period/school year change shows up as a hash mismatch, not a
    silent coercion."""

    snapshot = await connector.walk_resources(LeaId("lea-test-001"))

    sessions = snapshot["academic_sessions"]
    assert {row["id"] for row in sessions} == {
        "term-2026-fall",
        "term-2027-spring",
    }
    fall = next(r for r in sessions if r["id"] == "term-2026-fall")
    assert fall["session_type"] == "term"
    assert fall["school_year"] == "2026"
    assert fall["start_date"] == "2026-08-15"
    assert fall["end_date"] == "2026-12-19"


@pytest.mark.asyncio
async def test_walk_resources_filters_non_school_orgs(
    connector: EdLinkConnector,
) -> None:
    """Non-school orgs (the district itself) are dropped at the
    boundary so the canonical-side ``schools`` hash only carries
    school-typed rows."""

    snapshot = await connector.walk_resources(LeaId("lea-test-001"))

    schools = snapshot["schools"]
    assert {row["id"] for row in schools} == {"sch-001"}
    sch = schools[0]
    assert sch["name"] == "Lincoln Middle School"
    assert sch["school_code"] == "LMS-01"
    assert sch["parent_org_id"] == "lea-test-001"


@pytest.mark.asyncio
async def test_walk_resources_loops_until_has_more_false(
    edlink_fixtures_dir: Path,
    key_vault: KeyVaultClient,
    session_factory_placeholder: Any,
) -> None:
    """The walk drains a multi-page resource feed by passing the
    ``$next`` token back as ``next_token`` until the partner reports
    ``has_more=false``.

    Forces a tiny page size on the connector (1) so the
    lea-test-001 fixture's 2 enrollments come back across 2 pages.
    Without the loop, only the first page would land in the snapshot.
    """

    client = EdLinkClient(fixtures_dir=edlink_fixtures_dir)
    connector = EdLinkConnector(
        client=client,
        key_vault=key_vault,
        session_factory=session_factory_placeholder,
    )
    # Substitute a 1-row page size so the fixture's two enrollments
    # require two get_resources calls.
    connector.RESOURCE_PAGE_SIZE = 1  # type: ignore[misc]

    snapshot = await connector.walk_resources(LeaId("lea-test-001"))
    enrollment_ids = {row["id"] for row in snapshot["enrollments"]}
    assert enrollment_ids == {"enr-001", "enr-002"}


@pytest.mark.asyncio
async def test_walk_resources_missing_fixture_returns_empty_dict(
    connector: EdLinkConnector,
) -> None:
    """LEA without a resources fixture returns empty lists, not an error.

    Reconciliation against a freshly-authorized LEA whose snapshot
    fixture has not been authored yet still completes; the canonical
    side will also be empty so the run reports ``matched``.
    """

    snapshot = await connector.walk_resources(LeaId("lea-no-fixture"))
    assert snapshot == {
        "students": [],
        "enrollments": [],
        "classes": [],
        "academic_sessions": [],
        "schools": [],
    }


# ── set_cursor (DB-bound) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_cursor_writes_to_cursor_state(
    edlink_fixtures_dir: Path,
    key_vault: KeyVaultClient,
    db_session_factory: Any,
) -> None:
    """Operator cursor reset writes a row in cursor_state with the partner
    name set to 'edlink'. Re-running with a new cursor value updates the
    existing row (ON CONFLICT (lea_id, partner) DO UPDATE)."""

    connector = EdLinkConnector(
        client=EdLinkClient(fixtures_dir=edlink_fixtures_dir),
        key_vault=key_vault,
        session_factory=db_session_factory,
    )
    lea = LeaId(f"lea-cursor-test-{uuid.uuid4().hex[:8]}")
    observed = datetime(2026, 5, 19, 10, 0, 0)

    await connector.set_cursor(lea, Cursor(value="evt_001", observed_at=observed))

    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT last_event_id, partner FROM cursor_state "
                    "WHERE lea_id = :lea AND partner = 'edlink'"
                ),
                {"lea": lea},
            )
        ).one()
        assert row.last_event_id == "evt_001"
        assert row.partner == "edlink"

    # Rewind to a later cursor: upsert path.
    await connector.set_cursor(lea, Cursor(value="evt_007", observed_at=observed))

    async with db_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT last_event_id FROM cursor_state "
                    "WHERE lea_id = :lea AND partner = 'edlink'"
                ),
                {"lea": lea},
            )
        ).one()
        assert row.last_event_id == "evt_007"

        # Clean up so the test is repeatable.
        await session.execute(
            text("DELETE FROM cursor_state WHERE lea_id = :lea"),
            {"lea": lea},
        )
        await session.commit()


# ── walk_resources + reconciliation end-to-end (DB-bound) ────────────────────


@pytest.mark.asyncio
async def test_walk_resources_feeds_reconciliation_service(
    edlink_fixtures_dir: Path,
    key_vault: KeyVaultClient,
    db_session_factory: Any,
) -> None:
    """Walk + reconciliation against seeded canonical state matching the
    fixture reports ``matched`` on the entity-types both sides carry.

    The lea-test-001 resources fixture mirrors the post-event-drain
    canonical state (Alex re-graded to 06, stu-003 + enr-003 gone after
    the cascade). Seeding canonical directly to the same state and
    walking the resources fixture should hash to identical roots for
    the students entity-type. We assert matched for students; the
    full event-driven flow (which would also drop enr-003 via cascade)
    is out of scope for this test.
    """

    from edlink_rostering.services.reconciliation import ReconciliationService

    connector = EdLinkConnector(
        client=EdLinkClient(fixtures_dir=edlink_fixtures_dir),
        key_vault=key_vault,
        session_factory=db_session_factory,
    )
    lea = LeaId(f"lea-walk-recon-{uuid.uuid4().hex[:8]}")

    async with db_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leas (id, name, lea_type, state)
                VALUES (:id, 'Walk Recon Test', 'traditional_district', 'CA')
                """
            ),
            {"id": lea},
        )
        # Mirror the resources fixture exactly across all five
        # entity-types so every entry in _ENTITY_TABLES hashes to
        # match. Leaf-hash alignment requires the same column values
        # on both sides; the test seeds the canonical side directly
        # rather than running event-replay so the diff isolates the
        # walk + hash path.
        await session.execute(
            text(
                """
                INSERT INTO students (
                    id, lea_id, given_name, family_name, grade,
                    preferred_first_name, primary_school_id, external_ids
                ) VALUES
                  (:s1, :lea, 'Alex', 'Morgan', '06', NULL, 'sch-001',
                   CAST('{}' AS JSONB)),
                  (:s2, :lea, 'Bryn', 'Lee', '06', 'Brynn', 'sch-001',
                   CAST('{}' AS JSONB))
                """
            ),
            {"lea": lea, "s1": "stu-001", "s2": "stu-002"},
        )
        await session.execute(
            text(
                """
                INSERT INTO enrollments (
                    id, lea_id, student_id, class_id, begin_date, end_date
                ) VALUES
                  (:e1, :lea, :s1, 'cls-A', '2026-08-15', '2027-06-12'),
                  (:e2, :lea, :s2, 'cls-A', '2026-08-15', '2027-06-12')
                """
            ),
            {
                "lea": lea,
                "s1": "stu-001",
                "s2": "stu-002",
                "e1": "enr-001",
                "e2": "enr-002",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO classes (
                    id, lea_id, title, course_code, school_id, term_id
                ) VALUES
                  ('cls-A', :lea, 'Sixth Grade Homeroom A', '6-HR-A',
                   'sch-001', 'term-2026-fall')
                """
            ),
            {"lea": lea},
        )
        await session.execute(
            text(
                """
                INSERT INTO academic_sessions (
                    id, lea_id, title, session_type, school_year,
                    start_date, end_date
                ) VALUES
                  ('term-2026-fall', :lea, 'Fall 2026', 'term', '2026',
                   '2026-08-15', '2026-12-19'),
                  ('term-2027-spring', :lea, 'Spring 2027', 'term', '2026',
                   '2027-01-06', '2027-06-12')
                """
            ),
            {"lea": lea},
        )
        await session.execute(
            text(
                """
                INSERT INTO schools (
                    id, lea_id, name, school_code, parent_org_id
                ) VALUES
                  ('sch-001', :lea, 'Lincoln Middle School', 'LMS-01',
                   'lea-test-001')
                """
            ),
            {"lea": lea},
        )
        # Cursor older than the quiet window so the reconcile proceeds.
        await session.execute(
            text(
                """
                INSERT INTO cursor_state (
                    lea_id, partner, last_event_id, last_event_at,
                    last_poll_at, cold_start_required, updated_at
                ) VALUES (
                    :lea, 'edlink', 'evt_008',
                    NOW() - INTERVAL '2 hours',
                    NOW() - INTERVAL '2 hours',
                    false, NOW()
                )
                """
            ),
            {"lea": lea},
        )
        await session.commit()

    # Point the resources walk at lea-test-001's fixture by overriding
    # the lea_id in the snapshot callable. Production looks up the
    # fixture by lea_id directly; here we project the lea-test-001
    # fixture into the synthetic test LEA so cleanup is per-test.
    async def snapshot_for_test_lea(_lea_id: LeaId) -> dict[str, Any]:
        return await connector.walk_resources(LeaId("lea-test-001"))

    service = ReconciliationService(session_factory=db_session_factory)
    report = await service.reconcile_lea(
        lea_id=lea,
        partner="edlink",
        partner_snapshot=snapshot_for_test_lea,
    )

    assert report.status == "matched", (
        f"Expected matched, got {report.status}. Drift: {report.drift}"
    )
    assert report.canonical_root_hash == report.partner_root_hash

    # Cleanup.
    async with db_session_factory() as session:
        from tests.conftest import wipe_lea

        await session.execute(
            text("DELETE FROM reconciliation_runs WHERE lea_id = :lea"),
            {"lea": lea},
        )
        await wipe_lea(session, lea)
        await session.commit()
