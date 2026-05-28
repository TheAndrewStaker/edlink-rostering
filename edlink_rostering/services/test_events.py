"""Dev-only test-event dispatcher.

Lets the operator inject one of nine well-known scenarios at a chosen
LEA from the admin app. Each scenario walks the same code path the
polled worker would take if EdLink produced that page: a sync_jobs row
flips through `running` -> terminal, validation_results land for L1-L5
issues, quarantine rows open for L4, reconciliation_runs writes for the
drift scenario.

The dispatcher is what makes the live walkthrough story interactive.
It is not the production ingestion path; it lives behind the dev
profile gate and is gated by the `admin` role so the auditor
persona switcher cannot trigger writes.

The handler design exposes the running state visually:

1. Insert the sync_jobs row with status='running' and commit before the
   sleep so the dashboard's `in_flight` count picks it up within the
   next React Query poll cycle.
2. `asyncio.sleep(running_visibility_seconds)` so the running state
   stays on screen long enough for the operator to see the pill light
   up.
3. Run the scenario handler, which writes side effects (validation
   issues, quarantine rows, reconciliation_runs entries) and flips the
   same sync_jobs row to a terminal status (`success` or `failed`).

Each handler is small and self-contained because the realism we need
is "the right side effects land in the right tables" not "the full
validation pipeline ran." That keeps the dev surface honest about what
it is: a teaching tool for the walkthrough, not a simulation of the
production worker.

Scenario metadata (label, section, kind, description, sample data)
lives in `fixtures/edlink/test-events/<scenario_id>.json`. The router
serves the catalog so the React menu can render section dividers
without hardcoding the list on the frontend.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import structlog
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId
from edlink_rostering.infrastructure.ports import TelemetryFacade


logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


DEFAULT_VISIBILITY_SECONDS = 6.0
"""Default sleep between inserting the running row and finalizing.

React Query is configured with staleTime=5s + refetchInterval=10s for
the LEA list; 6s makes the running state visible on the next poll
without holding the demo flow too long.
"""


_PARTNER = "edlink"


@dataclass(frozen=True)
class TestEventScenario:
    """One walkthrough scenario the operator can fire from the drawer."""

    id: str
    label: str
    section: str
    kind: str
    description: str
    data: dict[str, Any]


@dataclass(frozen=True)
class DispatchResult:
    """Return shape from `enqueue` for the HTTP layer."""

    sync_job_id: uuid.UUID
    scenario_id: str
    lea_id: LeaId
    running_visibility_seconds: float


class ScenarioNotFound(LookupError):
    """Operator requested a scenario id that does not exist in the catalog."""


def load_scenarios(fixtures_dir: Path) -> dict[str, TestEventScenario]:
    """Read the test-events fixture directory into a scenario catalog.

    Each JSON file describes one scenario by `id`, `label`, `section`,
    `kind`, `description`, and free-form `data`. The dispatcher routes
    on `kind`; the rest is presentation metadata for the menu.
    """

    scenarios: dict[str, TestEventScenario] = {}
    if not fixtures_dir.exists():
        return scenarios
    for path in sorted(fixtures_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        scenario = TestEventScenario(
            id=str(raw["scenario_id"]),
            label=str(raw["label"]),
            section=str(raw["section"]),
            kind=str(raw["kind"]),
            description=str(raw["description"]),
            data=dict(raw.get("data") or {}),
        )
        scenarios[scenario.id] = scenario
    return scenarios


class TestEventService:
    """Dispatch one walkthrough scenario at a chosen LEA."""

    __test__ = False  # Tell pytest this is not a test class.

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        fixtures_dir: Path,
        telemetry: TelemetryFacade | None = None,
        running_visibility_seconds: float = DEFAULT_VISIBILITY_SECONDS,
    ) -> None:
        self._sessions = session_factory
        self._scenarios = load_scenarios(fixtures_dir)
        self._telemetry = telemetry
        self._visibility_seconds = running_visibility_seconds

    def list_scenarios(self) -> list[TestEventScenario]:
        """Return the scenario catalog. Order matches the section flow."""

        section_order = {
            "happy": 0,
            "validation": 1,
            "thresholds": 2,
            "other": 3,
        }
        return sorted(
            self._scenarios.values(),
            key=lambda s: (section_order.get(s.section, 9), s.id),
        )

    def get_scenario(self, scenario_id: str) -> TestEventScenario:
        try:
            return self._scenarios[scenario_id]
        except KeyError:
            raise ScenarioNotFound(scenario_id) from None

    async def enqueue(
        self,
        *,
        lea_id: LeaId,
        scenario_id: str,
        operator_subject: str,
    ) -> DispatchResult:
        """Insert the running row, schedule the background handler.

        The router awaits this method to get the sync_job_id back for
        the response, then returns to the client. The background task
        runs after the response is sent so the React app sees the
        running state within one poll cycle.
        """

        scenario = self.get_scenario(scenario_id)
        sync_job_id = uuid.uuid4()
        started_at = datetime.now(UTC)
        cursor_before = await self._read_current_cursor(lea_id)
        await self._insert_running_row(
            sync_job_id=sync_job_id,
            lea_id=lea_id,
            started_at=started_at,
            cursor_before=cursor_before,
        )
        logger.info(
            "test_event.enqueued",
            lea_id=lea_id,
            scenario_id=scenario_id,
            sync_job_id=str(sync_job_id),
            operator=operator_subject,
        )
        # Schedule the handler. The runtime owns the lifetime; we do
        # not await it. asyncio.create_task ensures the task is bound
        # to the running loop so the BackgroundTask infrastructure is
        # not required.
        asyncio.create_task(
            self._run_after_delay(
                sync_job_id=sync_job_id,
                lea_id=lea_id,
                scenario=scenario,
                cursor_before=cursor_before,
                operator_subject=operator_subject,
            )
        )
        return DispatchResult(
            sync_job_id=sync_job_id,
            scenario_id=scenario.id,
            lea_id=lea_id,
            running_visibility_seconds=self._visibility_seconds,
        )

    async def run_immediately(
        self,
        *,
        lea_id: LeaId,
        scenario_id: str,
        operator_subject: str,
    ) -> DispatchResult:
        """Run the scenario synchronously without the visibility sleep.

        Tests use this entry point so the handler effect lands before
        the assertion phase. The HTTP path uses `enqueue` instead so
        the running state is visible on the live admin app.
        """

        scenario = self.get_scenario(scenario_id)
        sync_job_id = uuid.uuid4()
        started_at = datetime.now(UTC)
        cursor_before = await self._read_current_cursor(lea_id)
        await self._insert_running_row(
            sync_job_id=sync_job_id,
            lea_id=lea_id,
            started_at=started_at,
            cursor_before=cursor_before,
        )
        await self._handle(
            sync_job_id=sync_job_id,
            lea_id=lea_id,
            scenario=scenario,
            cursor_before=cursor_before,
        )
        return DispatchResult(
            sync_job_id=sync_job_id,
            scenario_id=scenario.id,
            lea_id=lea_id,
            running_visibility_seconds=0.0,
        )

    # ── Internal: lifecycle ──────────────────────────────────────────

    async def _run_after_delay(
        self,
        *,
        sync_job_id: uuid.UUID,
        lea_id: LeaId,
        scenario: TestEventScenario,
        cursor_before: str | None,
        operator_subject: str,
    ) -> None:
        try:
            await asyncio.sleep(self._visibility_seconds)
            await self._handle(
                sync_job_id=sync_job_id,
                lea_id=lea_id,
                scenario=scenario,
                cursor_before=cursor_before,
            )
        except Exception:
            # The background task must never raise into the event loop.
            # Log the traceback so the operator can see what went wrong
            # in the API logs; flip the row to failed so the dashboard
            # does not leave a row stuck in `running` forever.
            logger.exception(
                "test_event.handler_failed",
                lea_id=lea_id,
                scenario_id=scenario.id,
                sync_job_id=str(sync_job_id),
                operator=operator_subject,
            )
            await self._force_failure(
                sync_job_id=sync_job_id,
                error_summary="test_event handler crashed",
            )

    async def _handle(
        self,
        *,
        sync_job_id: uuid.UUID,
        lea_id: LeaId,
        scenario: TestEventScenario,
        cursor_before: str | None,
    ) -> None:
        handler = _HANDLERS.get(scenario.kind)
        if handler is None:
            await self._force_failure(
                sync_job_id=sync_job_id,
                error_summary=f"unknown scenario kind {scenario.kind!r}",
            )
            return
        async with self._sessions() as session:
            await handler(
                session=session,
                sync_job_id=sync_job_id,
                lea_id=lea_id,
                cursor_before=cursor_before,
                data=scenario.data,
            )
            await session.commit()
        if self._telemetry is not None:
            self._telemetry.track_event(
                "test_event.dispatched",
                properties={
                    "lea_id": lea_id,
                    "scenario_id": scenario.id,
                    "scenario_kind": scenario.kind,
                    "sync_job_id": str(sync_job_id),
                },
            )

    async def _force_failure(
        self,
        *,
        sync_job_id: uuid.UUID,
        error_summary: str,
    ) -> None:
        async with self._sessions() as session:
            await _mark_terminal(
                session=session,
                sync_job_id=sync_job_id,
                status="failed",
                event_count=0,
                error_count=1,
                warning_count=0,
                cursor_after=None,
                error_summary=error_summary,
            )
            await session.commit()

    # ── Internal: reads / writes shared by handlers ──────────────────

    async def _read_current_cursor(self, lea_id: LeaId) -> str | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT last_event_id
                        FROM cursor_state
                        WHERE lea_id = :lea AND partner = :partner
                        """
                    ),
                    {"lea": lea_id, "partner": _PARTNER},
                )
            ).first()
        if row is None:
            return None
        value = row.last_event_id
        return None if value is None else str(value)

    async def _insert_running_row(
        self,
        *,
        sync_job_id: uuid.UUID,
        lea_id: LeaId,
        started_at: datetime,
        cursor_before: str | None,
    ) -> None:
        async with self._sessions() as session:
            await _ensure_lea(session, lea_id)
            await session.execute(
                text(
                    """
                    INSERT INTO sync_jobs (
                        id, lea_id, partner, status,
                        started_at, cursor_before
                    ) VALUES (
                        :id, :lea, :partner, 'running',
                        :started_at, :cursor_before
                    )
                    """
                ),
                {
                    "id": sync_job_id,
                    "lea": lea_id,
                    "partner": _PARTNER,
                    "started_at": started_at,
                    "cursor_before": cursor_before,
                },
            )
            await session.commit()


# ── Handlers ─────────────────────────────────────────────────────────


async def _ensure_lea(session: AsyncSession, lea_id: LeaId) -> None:
    """LEA placeholder upsert. Mirrors the sync worker's bootstrap.

    The dev demo seeds real LEAs; this is the safety net for an
    operator firing a scenario at a brand-new LEA that has not been
    onboarded yet. The placeholder keeps `lea_id` foreign keys happy.
    """

    await session.execute(
        text(
            """
            INSERT INTO leas (id, name, lea_type, state)
            VALUES (:id, :name, 'traditional_district', 'XX')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": lea_id, "name": f"LEA {lea_id}"},
    )


async def _mark_terminal(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    status: str,
    event_count: int,
    error_count: int,
    warning_count: int,
    cursor_after: str | None,
    error_summary: str | None,
) -> None:
    await session.execute(
        text(
            """
            UPDATE sync_jobs
            SET status = :status,
                completed_at = now(),
                event_count = :event_count,
                error_count = :error_count,
                warning_count = :warning_count,
                cursor_after = :cursor_after,
                error_summary = :error_summary
            WHERE id = :id
            """
        ),
        {
            "id": sync_job_id,
            "status": status,
            "event_count": event_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "cursor_after": cursor_after,
            "error_summary": error_summary,
        },
    )


async def _write_validation_issue(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    layer: int,
    code: str,
    event_id: str | None,
    detail: dict[str, Any],
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO sync_validation_results (
                sync_job_id, layer, code, payload_reference, detail,
                created_at
            ) VALUES (
                :sync_job_id, :layer, :code, :event_id,
                CAST(:detail AS JSONB), now()
            )
            """
        ),
        {
            "sync_job_id": sync_job_id,
            "layer": layer,
            "code": code,
            "event_id": event_id,
            "detail": json.dumps(detail),
        },
    )


async def _handle_happy_delta(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    cursor_before: str | None,
    data: dict[str, Any],
) -> None:
    """Insert one student + one enrollment plus snapshots; advance cursor.

    Counts visibly tick up on the LEA row. The cursor advances to a
    new event id so the cursor_after column on the timeline row
    differs from cursor_before.
    """

    suffix = str(data.get("student_suffix") or "tester")
    student_id = f"stu-test-{suffix}-{uuid.uuid4().hex[:6]}"
    enrollment_id = f"enr-test-{suffix}-{uuid.uuid4().hex[:6]}"
    cursor_after = f"evt_test_{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)

    # Canonical student.
    await session.execute(
        text(
            """
            INSERT INTO students (
                id, lea_id, given_name, family_name, grade,
                primary_school_id, external_ids
            ) VALUES (
                :id, :lea, :given, :family, :grade, :school,
                CAST(:external_ids AS JSONB)
            )
            ON CONFLICT (id) DO UPDATE SET
                given_name = EXCLUDED.given_name,
                family_name = EXCLUDED.family_name,
                grade = EXCLUDED.grade,
                primary_school_id = EXCLUDED.primary_school_id
            """
        ),
        {
            "id": student_id,
            "lea": lea_id,
            "given": str(data.get("given_name") or "Sam"),
            "family": str(data.get("family_name") or "Quincy"),
            "grade": str(data.get("grade") or "07"),
            "school": str(data.get("primary_school_id") or "sch-dev"),
            "external_ids": json.dumps({"sis": student_id}),
        },
    )

    # Student snapshot.
    student_payload = {
        "id": student_id,
        "lea_id": lea_id,
        "given_name": str(data.get("given_name") or "Sam"),
        "family_name": str(data.get("family_name") or "Quincy"),
        "grade": str(data.get("grade") or "07"),
    }
    await session.execute(
        text(
            """
            INSERT INTO student_snapshots (
                student_id, lea_id, generation_id, deleted_upstream,
                source_event_id, source_event_at, created_at, payload
            ) VALUES (
                :student_id, :lea, :gen, false,
                :source_event_id, :source_event_at, :created_at,
                CAST(:payload AS JSONB)
            )
            """
        ),
        {
            "student_id": student_id,
            "lea": lea_id,
            "gen": sync_job_id,
            "source_event_id": cursor_after,
            "source_event_at": now,
            "created_at": now,
            "payload": json.dumps(student_payload),
        },
    )

    # Canonical enrollment.
    begin = now.date()
    await session.execute(
        text(
            """
            INSERT INTO enrollments (
                id, lea_id, student_id, class_id, begin_date
            ) VALUES (
                :id, :lea, :student_id, :class_id, :begin
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": enrollment_id,
            "lea": lea_id,
            "student_id": student_id,
            "class_id": "cls-test-A",
            "begin": begin,
        },
    )

    enrollment_payload = {
        "id": enrollment_id,
        "lea_id": lea_id,
        "student_id": student_id,
        "class_id": "cls-test-A",
        "begin_date": begin.isoformat(),
    }
    await session.execute(
        text(
            """
            INSERT INTO enrollment_snapshots (
                enrollment_id, lea_id, generation_id, deleted_upstream,
                source_event_id, source_event_at, created_at, payload
            ) VALUES (
                :enrollment_id, :lea, :gen, false,
                :source_event_id, :source_event_at, :created_at,
                CAST(:payload AS JSONB)
            )
            """
        ),
        {
            "enrollment_id": enrollment_id,
            "lea": lea_id,
            "gen": sync_job_id,
            "source_event_id": cursor_after,
            "source_event_at": now,
            "created_at": now,
            "payload": json.dumps(enrollment_payload),
        },
    )

    # Cursor advance.
    await session.execute(
        text(
            """
            INSERT INTO cursor_state (
                lea_id, partner, last_event_id, last_event_at,
                last_poll_at, cold_start_required, updated_at
            ) VALUES (
                :lea, :partner, :last_event_id, :last_event_at,
                :last_poll_at, false, :updated_at
            )
            ON CONFLICT (lea_id, partner) DO UPDATE SET
                last_event_id = EXCLUDED.last_event_id,
                last_event_at = EXCLUDED.last_event_at,
                last_poll_at = EXCLUDED.last_poll_at,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "lea": lea_id,
            "partner": _PARTNER,
            "last_event_id": cursor_after,
            "last_event_at": now,
            "last_poll_at": now,
            "updated_at": now,
        },
    )

    await _mark_terminal(
        session=session,
        sync_job_id=sync_job_id,
        status="success",
        event_count=2,
        error_count=0,
        warning_count=0,
        cursor_after=cursor_after,
        error_summary=None,
    )


async def _handle_l1_failure(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    cursor_before: str | None,
    data: dict[str, Any],
) -> None:
    code = str(data.get("code") or "HTTP_INTEGRITY_FAILED")
    detail = {
        "http_status": int(data.get("http_status") or 200),
        "error": str(data.get("error") or "Layer 1 failure"),
        "severity": "error",
    }
    await _write_validation_issue(
        session=session,
        sync_job_id=sync_job_id,
        layer=1,
        code=code,
        event_id=None,
        detail=detail,
    )
    await _mark_terminal(
        session=session,
        sync_job_id=sync_job_id,
        status="failed",
        event_count=0,
        error_count=1,
        warning_count=0,
        cursor_after=cursor_before,
        error_summary=f"L1:{code}",
    )


async def _handle_l2_failure(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    cursor_before: str | None,
    data: dict[str, Any],
) -> None:
    code = str(data.get("code") or "SCHEMA_MISSING_FIELD")
    event_id = str(data.get("event_id") or "evt_test_l2")
    detail = {
        "field": str(data.get("field") or "givenName"),
        "severity": "error",
    }
    await _write_validation_issue(
        session=session,
        sync_job_id=sync_job_id,
        layer=2,
        code=code,
        event_id=event_id,
        detail=detail,
    )
    await _mark_terminal(
        session=session,
        sync_job_id=sync_job_id,
        status="failed",
        event_count=0,
        error_count=1,
        warning_count=0,
        cursor_after=cursor_before,
        error_summary=f"L2:{code}@{event_id}",
    )


async def _handle_l3_failure(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    cursor_before: str | None,
    data: dict[str, Any],
) -> None:
    """L3 errors are per-event. The page still commits; one event is
    rejected with a structured issue an operator can drill into."""

    code = str(data.get("code") or "PARSE_INVALID_DATE")
    event_id = str(data.get("event_id") or "evt_test_l3")
    detail = {
        "field": str(data.get("field") or "beginDate"),
        "raw_value": str(data.get("raw_value") or ""),
        "severity": "error",
    }
    await _write_validation_issue(
        session=session,
        sync_job_id=sync_job_id,
        layer=3,
        code=code,
        event_id=event_id,
        detail=detail,
    )
    # Cursor still advances to a new event id; the rejected event is
    # the one before the new high water mark.
    now = datetime.now(UTC)
    cursor_after = f"evt_test_{uuid.uuid4().hex[:8]}"
    await session.execute(
        text(
            """
            INSERT INTO cursor_state (
                lea_id, partner, last_event_id, last_event_at,
                last_poll_at, cold_start_required, updated_at
            ) VALUES (
                :lea, :partner, :last_event_id, :last_event_at,
                :last_poll_at, false, :updated_at
            )
            ON CONFLICT (lea_id, partner) DO UPDATE SET
                last_event_id = EXCLUDED.last_event_id,
                last_event_at = EXCLUDED.last_event_at,
                last_poll_at = EXCLUDED.last_poll_at,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "lea": lea_id,
            "partner": _PARTNER,
            "last_event_id": cursor_after,
            "last_event_at": now,
            "last_poll_at": now,
            "updated_at": now,
        },
    )
    await _mark_terminal(
        session=session,
        sync_job_id=sync_job_id,
        status="success",
        event_count=0,
        error_count=1,
        warning_count=0,
        cursor_after=cursor_after,
        error_summary=f"L3:{code}@{event_id}",
    )


async def _handle_l4_orphan(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    cursor_before: str | None,
    data: dict[str, Any],
) -> None:
    """Open one quarantine row for an enrollment whose student is missing.

    The sync_jobs row finishes `success` because the page committed
    everything that was not the orphan. The quarantine queue panel on
    the drawer reflects the new unresolved row.
    """

    prefix = str(data.get("enrollment_id_prefix") or "enr-test-orph")
    enrollment_id = f"{prefix}-{uuid.uuid4().hex[:6]}"
    missing_student_id = str(data.get("missing_student_id") or "stu-MISSING")
    raw_payload = {
        "id": enrollment_id,
        "student_id": missing_student_id,
        "class_id": "cls-test-A",
        "begin_date": datetime.now(UTC).date().isoformat(),
        "source_event_id": f"evt_test_{uuid.uuid4().hex[:8]}",
    }
    await session.execute(
        text(
            """
            INSERT INTO quarantine (
                sync_job_id, lea_id, entity_type, entity_id, reason,
                raw_payload, created_at
            ) VALUES (
                :sync_job_id, :lea, 'enrollment', :entity_id, :reason,
                CAST(:raw_payload AS JSONB), now()
            )
            """
        ),
        {
            "sync_job_id": sync_job_id,
            "lea": lea_id,
            "entity_id": enrollment_id,
            "reason": (
                "Layer 4: enrollment references unknown student "
                f"{missing_student_id}"
            ),
            "raw_payload": json.dumps(raw_payload),
        },
    )
    await _write_validation_issue(
        session=session,
        sync_job_id=sync_job_id,
        layer=4,
        code="ENROLLMENT_ORPHAN_STUDENT",
        event_id=raw_payload["source_event_id"],
        detail={
            "missing_student_id": missing_student_id,
            "entity_type": "enrollment",
            "entity_id": enrollment_id,
            "severity": "error",
        },
    )
    # Advance cursor: the rest of the page committed.
    now = datetime.now(UTC)
    cursor_after = raw_payload["source_event_id"]
    await session.execute(
        text(
            """
            INSERT INTO cursor_state (
                lea_id, partner, last_event_id, last_event_at,
                last_poll_at, cold_start_required, updated_at
            ) VALUES (
                :lea, :partner, :last_event_id, :last_event_at,
                :last_poll_at, false, :updated_at
            )
            ON CONFLICT (lea_id, partner) DO UPDATE SET
                last_event_id = EXCLUDED.last_event_id,
                last_event_at = EXCLUDED.last_event_at,
                last_poll_at = EXCLUDED.last_poll_at,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "lea": lea_id,
            "partner": _PARTNER,
            "last_event_id": cursor_after,
            "last_event_at": now,
            "last_poll_at": now,
            "updated_at": now,
        },
    )
    await _mark_terminal(
        session=session,
        sync_job_id=sync_job_id,
        status="success",
        event_count=0,
        error_count=1,
        warning_count=0,
        cursor_after=cursor_after,
        error_summary="L4:ENROLLMENT_ORPHAN_STUDENT",
    )


async def _handle_l5_threshold(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    cursor_before: str | None,
    data: dict[str, Any],
) -> None:
    """L5 thresholds page-block: the sync fails and no canonical change lands."""

    code = str(data.get("code") or "THRESHOLD_EVENT_VOLUME_SPIKE")
    detail = {
        k: v
        for k, v in data.items()
        if k in {
            "page_event_count",
            "baseline_median",
            "ratio",
            "live_count_before",
            "projected_count_after",
            "shift_pct",
        }
    }
    detail["severity"] = "error"
    await _write_validation_issue(
        session=session,
        sync_job_id=sync_job_id,
        layer=5,
        code=code,
        event_id=None,
        detail=detail,
    )
    await _mark_terminal(
        session=session,
        sync_job_id=sync_job_id,
        status="failed",
        event_count=0,
        error_count=1,
        warning_count=0,
        cursor_after=cursor_before,
        error_summary=f"L5:{code}",
    )


async def _handle_reconciliation_drift(
    *,
    session: AsyncSession,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    cursor_before: str | None,
    data: dict[str, Any],
) -> None:
    """Write a drift_detected reconciliation_runs row.

    The sync_jobs placeholder finishes `success` with zero events so
    the in-flight indicator clears; the visible drift lands in the
    reconciliation section of the drawer plus the alerts banner.
    """

    entity_type = str(data.get("entity_type") or "students")
    canonical_only_ids = list(data.get("canonical_only_ids") or [])
    partner_only_ids = list(data.get("partner_only_ids") or [])

    # Synthesize plausible hashes so the drawer renders short cursors
    # rather than blank strings.
    canonical_root_hash = hashlib.sha256(
        f"canonical:{lea_id}:{uuid.uuid4().hex}".encode()
    ).hexdigest()
    partner_root_hash = hashlib.sha256(
        f"partner:{lea_id}:{uuid.uuid4().hex}".encode()
    ).hexdigest()
    drift_summary = [
        {
            "entity_type": entity_type,
            "canonical_only_ids": canonical_only_ids,
            "partner_only_ids": partner_only_ids,
            "canonical_mid_hash": canonical_root_hash[:16],
            "partner_mid_hash": partner_root_hash[:16],
        }
    ]
    now = datetime.now(UTC)
    started = now - timedelta(seconds=1)
    await session.execute(
        text(
            """
            INSERT INTO reconciliation_runs (
                lea_id, partner, started_at, completed_at, status,
                canonical_root_hash, partner_root_hash, drift_summary
            ) VALUES (
                :lea, :partner, :started, :completed, 'drift_detected',
                :canonical_root_hash, :partner_root_hash,
                CAST(:drift_summary AS JSONB)
            )
            """
        ),
        {
            "lea": lea_id,
            "partner": _PARTNER,
            "started": started,
            "completed": now,
            "canonical_root_hash": canonical_root_hash,
            "partner_root_hash": partner_root_hash,
            "drift_summary": json.dumps(drift_summary),
        },
    )
    await _mark_terminal(
        session=session,
        sync_job_id=sync_job_id,
        status="success",
        event_count=0,
        error_count=0,
        warning_count=1,
        cursor_after=cursor_before,
        error_summary="reconciliation_drift: see reconciliation_runs",
    )


_HANDLERS = {
    "happy_delta": _handle_happy_delta,
    "l1_failure": _handle_l1_failure,
    "l2_failure": _handle_l2_failure,
    "l3_failure": _handle_l3_failure,
    "l4_orphan": _handle_l4_orphan,
    "l5_threshold": _handle_l5_threshold,
    "reconciliation_drift": _handle_reconciliation_drift,
}


__all__ = [
    "DEFAULT_VISIBILITY_SECONDS",
    "DispatchResult",
    "ScenarioNotFound",
    "TestEventScenario",
    "TestEventService",
    "load_scenarios",
]
