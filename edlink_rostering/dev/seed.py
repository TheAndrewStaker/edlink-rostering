"""Dev-only seed for a presentable admin surface.

Seeds five LEAs that exercise distinct operational states so the
admin app looks "lived-in" rather than blank. Each LEA's state is the
result the operator would see after a realistic ingest history. The
seed does not run a sync; it inserts canonical + snapshot + audit rows
directly so the state is deterministic and fast.

The five LEAs:

1. **Lakewood USD (CA)** — happy path. 8 students, 6 enrollments,
   five recent successful sync_jobs to give Layer 5 a baseline.

2. **Northridge SD (WA)** — happy path with a single
   reverted sync in history. Used to demo "the timeline keeps a
   honest record of operator actions".

3. **Valley Charter (CA, charter LEA)** — most-recent sync failed.
   Drives the sync_failure + schema_drift alerts. The operator can
   click Retry from the admin app.

4. **Hillcrest USD (CA, charter_lea)** — quarantine backlog.
   30 unresolved orphan enrollments waiting on student rows that did
   not arrive. Drives the quarantine_growth alert and gives the
   operator something to release/reject.

5. **Riverside USD (CA)** — cursor 25 days behind. Drives the
   cursor_lag_20_day alert with 5-day headroom before the 30-day
   retention ceiling forces a cold start.

All UUIDs are deterministic so seed restarts are idempotent. Date
fields that should refresh on every seed (the stale cursor, the
recent sync timestamps) use ``datetime.now(UTC)`` plus relative
offsets and are written via an UPDATE pass after the INSERT. The
pattern mirrors ``DevSeedConfig`` in the benefits-administration
project where the founder kept dev data presentable across restarts.

Entry point: :func:`seed_realistic_state` (async). Invoked from
``scripts/seed-dev.sh`` via ``python -m edlink_rostering.dev.seed``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from edlink_rostering.core.types import LeaId


# ── Deterministic identifiers ────────────────────────────────────────────────
#
# LEA ids and sync_job UUIDs are stable across seed runs so the admin
# app shows the same rows after each restart and the reset scripts can
# target them without name-based lookups. Pattern matches the
# benefits-administration UUID convention.


@dataclass(frozen=True)
class SeededLea:
    id: LeaId
    name: str
    lea_type: str
    state: str
    nces_lea_id: str | None
    note: str
    status: str = "active"
    timezone: str = "America/Los_Angeles"
    edlink_integration_id: str | None = None


@dataclass(frozen=True)
class SeededOperator:
    """An operator persona seeded into the local dev DB.

    ``authorized_leas`` is the explicit LEA scope for the auth tests.
    ``None`` means "implicit all" (owner and admin
    roles see everything at the auth-module layer; the seed inserts
    no per-LEA grants for them).
    """

    subject: str
    email: str
    display_name: str
    role: str
    authorized_leas: tuple[LeaId, ...] | None


SEEDED_OPERATORS: tuple[SeededOperator, ...] = (
    SeededOperator(
        subject="stephen-dev-001",
        email="stephen@edlink.test",
        display_name="Stephen Staker",
        role="owner",
        authorized_leas=None,
    ),
    SeededOperator(
        subject="admin-dev-001",
        email="admin@edlink.test",
        display_name="Admin User",
        role="owner",
        authorized_leas=None,
    ),
    SeededOperator(
        subject="qa-dev-001",
        email="qa@edlink.test",
        display_name="QA Dev",
        role="admin",
        authorized_leas=None,
    ),
    SeededOperator(
        subject="lakewood-ops-001",
        email="lakewood@edlink.test",
        display_name="Lakewood Ops",
        role="operator",
        authorized_leas=(LeaId("lea-lakewood-usd"),),
    ),
    SeededOperator(
        subject="district-ops-001",
        email="ops@edlink.test",
        display_name="District Ops",
        role="operator",
        authorized_leas=(LeaId("lea-riverside-usd"),),
    ),
    SeededOperator(
        subject="auditor-001",
        email="auditor@edlink.test",
        display_name="Read-only Auditor",
        role="auditor",
        authorized_leas=None,
    ),
)


SEEDED_LEAS: tuple[SeededLea, ...] = (
    SeededLea(
        id=LeaId("lea-lakewood-usd"),
        name="Lakewood Unified School District",
        lea_type="traditional_district",
        state="CA",
        nces_lea_id="0600001",
        note="happy path; full sync history for Layer 5 baseline",
        status="active",
        timezone="America/Los_Angeles",
        edlink_integration_id="edlink-int-lakewood-usd",
    ),
    SeededLea(
        id=LeaId("lea-northridge-sd"),
        name="Northridge School District",
        lea_type="traditional_district",
        state="WA",
        nces_lea_id="5300001",
        note="happy path with one reverted sync in history",
        status="active",
        timezone="America/Los_Angeles",
        edlink_integration_id="edlink-int-northridge-sd",
    ),
    SeededLea(
        id=LeaId("lea-valley-charter"),
        name="Valley Charter",
        lea_type="charter_lea",
        state="CA",
        nces_lea_id="0600002",
        note="latest sync failed; drives sync_failure alert",
        status="active",
        timezone="America/Los_Angeles",
        edlink_integration_id="edlink-int-valley-charter",
    ),
    SeededLea(
        id=LeaId("lea-hillcrest-usd"),
        name="Hillcrest USD",
        lea_type="charter_lea",
        state="CA",
        nces_lea_id="0600003",
        note="quarantine backlog; drives quarantine_growth alert",
        status="onboarding",
        timezone="America/Los_Angeles",
        edlink_integration_id="edlink-int-hillcrest-usd",
    ),
    SeededLea(
        id=LeaId("lea-riverside-usd"),
        name="Riverside Unified School District",
        lea_type="traditional_district",
        state="CA",
        nces_lea_id="0600004",
        note="stale cursor; drives cursor_lag_20_day alert",
        status="active",
        timezone="America/Los_Angeles",
        edlink_integration_id="edlink-int-riverside-usd",
    ),
)


def _seed_uuid(namespace: str, key: str) -> uuid.UUID:
    """Stable UUID v5 derived from a namespace + key.

    The seed never picks random UUIDs so a restart updates the same
    row rather than appending a new one.
    """

    return uuid.uuid5(
        uuid.UUID("00000000-0000-0000-0000-000000000000"), f"{namespace}:{key}"
    )


async def seed_realistic_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Insert the five seeded LEAs and their state.

    Idempotent: subsequent runs update date-sensitive rows (so the
    cursor-lag scenario still shows ~25 days behind no matter when the
    seed runs) and leave the rest untouched via ON CONFLICT DO NOTHING.
    """

    now = datetime.now(UTC)
    async with session_factory() as session:
        await _seed_leas(session)
        await _seed_lakewood(session, now)
        await _seed_northridge(session, now)
        await _seed_valley(session, now)
        await _seed_hillcrest(session, now)
        await _seed_riverside(session, now)
        await _seed_sync_activity_history(session, now)
        await _seed_operators(session)
        await _seed_connector_authorizations(session, now)
        await _seed_operator_lea_grants(session)
        await _seed_reconciliation_runs(session, now)
        await session.commit()


# ── LEAs ─────────────────────────────────────────────────────────────────────


async def _seed_leas(session: AsyncSession) -> None:
    for lea in SEEDED_LEAS:
        await session.execute(
            text(
                """
                INSERT INTO leas (
                    id, name, lea_type, state, nces_lea_id,
                    status, timezone, edlink_integration_id
                )
                VALUES (
                    :id, :name, :lea_type, :state, :nces,
                    :status, :tz, :integration
                )
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    lea_type = EXCLUDED.lea_type,
                    state = EXCLUDED.state,
                    nces_lea_id = EXCLUDED.nces_lea_id,
                    status = EXCLUDED.status,
                    timezone = EXCLUDED.timezone,
                    edlink_integration_id = EXCLUDED.edlink_integration_id
                """
            ),
            {
                "id": lea.id,
                "name": lea.name,
                "lea_type": lea.lea_type,
                "state": lea.state,
                "nces": lea.nces_lea_id,
                "status": lea.status,
                "tz": lea.timezone,
                "integration": lea.edlink_integration_id,
            },
        )


# ── Lakewood: happy path with sync history ────────────────────────────────────


async def _seed_lakewood(session: AsyncSession, now: datetime) -> None:
    lea_id = LeaId("lea-lakewood-usd")
    students = [
        ("stu-lkw-001", "Aiko", "Tanaka", "07"),
        ("stu-lkw-002", "Benjamin", "Cohen", "07"),
        ("stu-lkw-003", "Chloe", "Wright", "08"),
        ("stu-lkw-004", "Diego", "Hernandez", "08"),
        ("stu-lkw-005", "Emma", "Patel", "06"),
        ("stu-lkw-006", "Felix", "Nguyen", "06"),
        ("stu-lkw-007", "Grace", "Kim", "07"),
        ("stu-lkw-008", "Henry", "Lopez", "07"),
    ]
    enrollments = [
        ("enr-lkw-001", "stu-lkw-001", "cls-math-7a"),
        ("enr-lkw-002", "stu-lkw-002", "cls-math-7a"),
        ("enr-lkw-003", "stu-lkw-003", "cls-math-8a"),
        ("enr-lkw-004", "stu-lkw-004", "cls-math-8a"),
        ("enr-lkw-005", "stu-lkw-007", "cls-math-7b"),
        ("enr-lkw-006", "stu-lkw-008", "cls-math-7b"),
    ]
    await _seed_canonical(session, lea_id, students, enrollments, now)
    # Five successful sync_jobs across the last week so Layer 5 has a
    # baseline median. Page sizes vary slightly to make the dashboard
    # numbers feel real.
    page_counts = [14, 12, 15, 11, 13]
    for offset_hours, evt_count in enumerate(page_counts):
        await _insert_sync_job(
            session,
            sync_job_id=_seed_uuid("lakewood-sync", str(offset_hours)),
            lea_id=lea_id,
            status="success",
            started_at=now - timedelta(hours=offset_hours * 24),
            event_count=evt_count,
            cursor_before=f"evt_{1000 + offset_hours * evt_count:05d}",
            cursor_after=f"evt_{1000 + offset_hours * evt_count + evt_count:05d}",
        )
    await _upsert_cursor(
        session,
        lea_id=lea_id,
        last_event_id="evt_01065",
        last_event_at=now - timedelta(minutes=12),
        last_poll_at=now - timedelta(minutes=4),
    )


# ── Northridge: happy path with one reverted sync ─────────────────────────────


async def _seed_northridge(session: AsyncSession, now: datetime) -> None:
    lea_id = LeaId("lea-northridge-sd")
    students = [
        ("stu-nrd-001", "Iris", "Anderson", "05"),
        ("stu-nrd-002", "Jamie", "Brooks", "05"),
        ("stu-nrd-003", "Kai", "Chen", "06"),
        ("stu-nrd-004", "Liam", "Davies", "06"),
        ("stu-nrd-005", "Mira", "Singh", "07"),
    ]
    enrollments = [
        ("enr-nrd-001", "stu-nrd-001", "cls-nrd-5a"),
        ("enr-nrd-002", "stu-nrd-002", "cls-nrd-5a"),
        ("enr-nrd-003", "stu-nrd-003", "cls-nrd-6a"),
        ("enr-nrd-004", "stu-nrd-005", "cls-nrd-7a"),
    ]
    await _seed_canonical(session, lea_id, students, enrollments, now)
    # Three successes followed by one revert synthetic row, then two
    # more successes. Lets the admin app's timeline show a real
    # operator-initiated rollback.
    timeline = [
        ("success", 12, 6),
        ("success", 10, 5),
        ("success", 8, 4),
        ("revert", 0, 3),
        ("success", 11, 2),
        ("success", 9, 1),
    ]
    for idx, (status, evt_count, hours_ago) in enumerate(timeline):
        await _insert_sync_job(
            session,
            sync_job_id=_seed_uuid("northridge-sync", str(idx)),
            lea_id=lea_id,
            status=status,
            started_at=now - timedelta(hours=hours_ago * 6),
            event_count=evt_count,
            cursor_before=f"evt_nrd_{idx:03d}_before",
            cursor_after=(
                f"evt_nrd_{idx:03d}_after" if status == "success" else None
            ),
            error_summary=(
                "revert of sync_job earlier-001"
                if status == "revert"
                else None
            ),
        )
    await _upsert_cursor(
        session,
        lea_id=lea_id,
        last_event_id="evt_nrd_005_after",
        last_event_at=now - timedelta(hours=5),
        last_poll_at=now - timedelta(minutes=5),
    )


# ── Valley Charter: failed sync drives the sync_failure alert ─────────────────


async def _seed_valley(session: AsyncSession, now: datetime) -> None:
    lea_id = LeaId("lea-valley-charter")
    students = [
        ("stu-val-001", "Noah", "Roberts", "09"),
        ("stu-val-002", "Olivia", "Sanchez", "09"),
        ("stu-val-003", "Parker", "Thompson", "10"),
    ]
    enrollments = [
        ("enr-val-001", "stu-val-001", "cls-val-9a"),
        ("enr-val-002", "stu-val-002", "cls-val-9a"),
    ]
    await _seed_canonical(session, lea_id, students, enrollments, now)
    # One earlier success then a failed sync. The failed sync's
    # cursor_after is null and error_summary names the Layer 2 issue
    # so the schema_drift alert path is realistic.
    success_id = _seed_uuid("valley-sync", "ok")
    failed_id = _seed_uuid("valley-sync", "failed")
    await _insert_sync_job(
        session,
        sync_job_id=success_id,
        lea_id=lea_id,
        status="success",
        started_at=now - timedelta(hours=14),
        event_count=8,
        cursor_before="evt_val_pre",
        cursor_after="evt_val_post",
    )
    await _insert_sync_job(
        session,
        sync_job_id=failed_id,
        lea_id=lea_id,
        status="failed",
        started_at=now - timedelta(minutes=22),
        event_count=0,
        cursor_before="evt_val_post",
        cursor_after=None,
        error_summary="L2:SCHEMA_MISSING_FIELD@evt_val_010",
        error_count=3,
    )
    # Attach a Layer 2 validation row so the schema-drift alert
    # evaluator and the admin app's sync detail view both fire.
    await _insert_validation_issue(
        session,
        sync_job_id=failed_id,
        layer=2,
        code="SCHEMA_MISSING_FIELD",
        payload_reference="evt_val_010",
        detail={
            "field": "givenName",
            "entity_type": "student",
            "severity": "error",
        },
        created_at=now - timedelta(minutes=22),
    )
    await _upsert_cursor(
        session,
        lea_id=lea_id,
        last_event_id="evt_val_post",
        last_event_at=now - timedelta(hours=14),
        last_poll_at=now - timedelta(minutes=22),
    )


# ── Hillcrest: quarantine backlog drives quarantine_growth ────────────────────


async def _seed_hillcrest(session: AsyncSession, now: datetime) -> None:
    lea_id = LeaId("lea-hillcrest-usd")
    students = [
        ("stu-hcr-001", "Quinn", "Ortiz", "11"),
        ("stu-hcr-002", "Riley", "Park", "11"),
    ]
    enrollments = [
        ("enr-hcr-001", "stu-hcr-001", "cls-hcr-11a"),
        ("enr-hcr-002", "stu-hcr-002", "cls-hcr-11a"),
    ]
    await _seed_canonical(session, lea_id, students, enrollments, now)
    sync_job_id = _seed_uuid("hillcrest-sync", "ok")
    await _insert_sync_job(
        session,
        sync_job_id=sync_job_id,
        lea_id=lea_id,
        status="success",
        started_at=now - timedelta(hours=2),
        event_count=4,
        warning_count=30,
        cursor_before="evt_hcr_pre",
        cursor_after="evt_hcr_post",
    )
    # 30 orphan enrollments referencing students that never arrived.
    # Crosses the 25-unresolved-row default threshold for the alert.
    for i in range(30):
        await _insert_quarantine_row(
            session,
            quarantine_id=_seed_uuid("hillcrest-quar", str(i)),
            sync_job_id=sync_job_id,
            lea_id=lea_id,
            entity_type="enrollment",
            entity_id=f"enr-hcr-orph-{i:03d}",
            reason="Layer 4: referential dependency unresolved",
            raw_payload={
                "id": f"enr-hcr-orph-{i:03d}",
                "lea_id": lea_id,
                "student_id": f"stu-hcr-missing-{i:03d}",
                "class_id": "cls-hcr-11a",
                "begin_date": str(date(2026, 8, 15)),
                "end_date": None,
                "source_event_id": f"evt_hcr_orph_{i:03d}",
            },
            created_at=now - timedelta(minutes=120 - i),
        )
    await _upsert_cursor(
        session,
        lea_id=lea_id,
        last_event_id="evt_hcr_post",
        last_event_at=now - timedelta(hours=2),
        last_poll_at=now - timedelta(minutes=5),
    )


# ── Riverside: cursor 25 days behind drives cursor_lag_20_day ─────────────────


async def _seed_riverside(session: AsyncSession, now: datetime) -> None:
    lea_id = LeaId("lea-riverside-usd")
    students = [
        ("stu-rvs-001", "Sage", "Underwood", "03"),
        ("stu-rvs-002", "Theo", "Vasquez", "03"),
        ("stu-rvs-003", "Uma", "Walker", "04"),
    ]
    enrollments = [
        ("enr-rvs-001", "stu-rvs-001", "cls-rvs-3a"),
        ("enr-rvs-002", "stu-rvs-002", "cls-rvs-3a"),
    ]
    await _seed_canonical(session, lea_id, students, enrollments, now)
    await _insert_sync_job(
        session,
        sync_job_id=_seed_uuid("riverside-sync", "last"),
        lea_id=lea_id,
        status="success",
        started_at=now - timedelta(days=25),
        event_count=11,
        cursor_before="evt_rvs_pre",
        cursor_after="evt_rvs_post",
    )
    # The stale cursor is the load-bearing piece. UPDATE pass on every
    # restart so it stays exactly 25 days behind no matter when seed
    # runs.
    stale_at = now - timedelta(days=25)
    await _upsert_cursor(
        session,
        lea_id=lea_id,
        last_event_id="evt_rvs_post",
        last_event_at=stale_at,
        last_poll_at=stale_at,
    )
    await session.execute(
        text(
            """
            UPDATE cursor_state
            SET last_event_at = :stale_at,
                last_poll_at = :stale_at
            WHERE lea_id = :lea AND partner = 'edlink'
            """
        ),
        {"lea": lea_id, "stale_at": stale_at},
    )


# ── Sync activity: 24h chart seed ────────────────────────────────────────────


async def _seed_sync_activity_history(
    session: AsyncSession, now: datetime
) -> None:
    """Seed sync_jobs spread across the last 24 hours for the activity chart.

    Distributes ~60 syncs across four LEAs (Lakewood, Northridge,
    Hillcrest, Valley Charter) with realistic cadence: roughly one sync
    per LEA every 90 minutes. Outcomes are predominantly success with
    a few warnings and one failure to make the chart visually
    informative without being alarming.

    These are additional to each LEA's scenario-specific sync_jobs.
    """

    leas = [
        LeaId("lea-lakewood-usd"),
        LeaId("lea-northridge-sd"),
        LeaId("lea-hillcrest-usd"),
        LeaId("lea-valley-charter"),
    ]

    # Schedule: every 90 minutes per LEA over the last 24h gives
    # ~16 syncs per LEA, ~64 total. Stagger LEAs by 20 minutes so
    # they don't all fire at the same wall-clock minute.
    syncs_per_lea = 16
    interval_minutes = 90

    for lea_idx, lea_id in enumerate(leas):
        stagger = timedelta(minutes=lea_idx * 20)
        for i in range(syncs_per_lea):
            started_at = (
                now
                - timedelta(hours=24)
                + timedelta(minutes=i * interval_minutes)
                + stagger
            )
            if started_at > now:
                break

            # Outcome distribution: mostly success, occasional warning,
            # rare failure. Deterministic per (lea_idx, i) so reruns
            # are stable.
            slot = (lea_idx * syncs_per_lea + i) % 20
            if slot == 17:
                status = "failed"
                error_count = 2
                warning_count = 0
                event_count = 3
                error_summary = "L3:HTTP_INTEGRITY_FAILED@evt_act_err"
            elif slot in (5, 11, 14):
                status = "success"
                error_count = 0
                warning_count = 2
                event_count = 8 + (i % 5)
                error_summary = None
            else:
                status = "success"
                error_count = 0
                warning_count = 0
                event_count = 10 + (i % 7)
                error_summary = None

            await _insert_sync_job(
                session,
                sync_job_id=_seed_uuid("activity", f"{lea_id}:{i}"),
                lea_id=lea_id,
                status=status,
                started_at=started_at,
                event_count=event_count,
                error_count=error_count,
                warning_count=warning_count,
                cursor_before=f"evt_act_{lea_idx:02d}_{i:02d}_pre",
                cursor_after=(
                    f"evt_act_{lea_idx:02d}_{i:02d}_post"
                    if status != "failed"
                    else None
                ),
                error_summary=error_summary,
            )


# ── Shared helpers ───────────────────────────────────────────────────────────


async def _seed_canonical(
    session: AsyncSession,
    lea_id: LeaId,
    students: Iterable[tuple[str, str, str, str]],
    enrollments: Iterable[tuple[str, str, str]],
    now: datetime,
) -> None:
    """Insert students, enrollments, and the per-LEA referenced rows in
    ``schools``, ``classes``, and ``academic_sessions``.

    V0010 expanded the canonical model to cover all five OneRoster
    resource families. The reconciliation Merkle hash now folds in
    classes, academic_sessions, and schools alongside students and
    enrollments. The seed has to write at least one row per
    new table so a forced ``edlink-rostering reconcile`` against the
    partner-side fixture has something to compare to; without the
    rows the partner walk would always report drift on the three
    new types.

    Each LEA gets:

    - one ``schools`` row per LEA (the LEA's primary school, with
      id ``sch-<lea-suffix>``)
    - one ``academic_sessions`` row per LEA (the ``term-2026-fall``
      session for the demo school year)
    - one ``classes`` row per unique ``class_id`` in the enrollment
      tuples, attached to that LEA's school and the fall term
    """

    suffix = lea_id.removeprefix("lea-") or lea_id
    school_id = f"sch-{suffix}"
    term_id = f"term-{suffix}-2026-fall"

    await session.execute(
        text(
            """
            INSERT INTO schools (id, lea_id, name, school_code, parent_org_id)
            VALUES (:id, :lea, :name, :code, :parent)
            ON CONFLICT (id) DO UPDATE SET
                lea_id = EXCLUDED.lea_id,
                name = EXCLUDED.name,
                school_code = EXCLUDED.school_code,
                parent_org_id = EXCLUDED.parent_org_id
            """
        ),
        {
            "id": school_id,
            "lea": lea_id,
            "name": f"{suffix.replace('-', ' ').title()} Primary",
            "code": suffix.upper(),
            "parent": lea_id,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO academic_sessions (
                id, lea_id, title, session_type, school_year,
                start_date, end_date
            ) VALUES (
                :id, :lea, :title, 'term', '2026', :start, :end
            )
            ON CONFLICT (id) DO UPDATE SET
                lea_id = EXCLUDED.lea_id,
                title = EXCLUDED.title,
                session_type = EXCLUDED.session_type,
                school_year = EXCLUDED.school_year,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date
            """
        ),
        {
            "id": term_id,
            "lea": lea_id,
            "title": "Fall 2026",
            "start": date(2026, 8, 15),
            "end": date(2026, 12, 19),
        },
    )

    for stu_id, given, family, grade in students:
        await session.execute(
            text(
                """
                INSERT INTO students (
                    id, lea_id, given_name, family_name, grade,
                    primary_school_id, external_ids
                ) VALUES (
                    :id, :lea, :given, :family, :grade,
                    :school, CAST('{}' AS JSONB)
                )
                ON CONFLICT (id) DO UPDATE SET
                    lea_id = EXCLUDED.lea_id,
                    given_name = EXCLUDED.given_name,
                    family_name = EXCLUDED.family_name,
                    grade = EXCLUDED.grade,
                    primary_school_id = EXCLUDED.primary_school_id
                """
            ),
            {
                "id": stu_id,
                "lea": lea_id,
                "given": given,
                "family": family,
                "grade": grade,
                "school": school_id,
            },
        )
    seen_classes: set[str] = set()
    for enr_id, stu_id, class_id in enrollments:
        if class_id not in seen_classes:
            seen_classes.add(class_id)
            await session.execute(
                text(
                    """
                    INSERT INTO classes (
                        id, lea_id, title, course_code,
                        school_id, term_id
                    ) VALUES (
                        :id, :lea, :title, :code, :school, :term
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        lea_id = EXCLUDED.lea_id,
                        title = EXCLUDED.title,
                        course_code = EXCLUDED.course_code,
                        school_id = EXCLUDED.school_id,
                        term_id = EXCLUDED.term_id
                    """
                ),
                {
                    "id": class_id,
                    "lea": lea_id,
                    "title": _class_title_for(class_id),
                    "code": class_id.upper(),
                    "school": school_id,
                    "term": term_id,
                },
            )
        await session.execute(
            text(
                """
                INSERT INTO enrollments (
                    id, lea_id, student_id, class_id, begin_date, end_date
                ) VALUES (
                    :id, :lea, :stu, :cls, :begin, :end
                )
                ON CONFLICT (id) DO UPDATE SET
                    lea_id = EXCLUDED.lea_id,
                    student_id = EXCLUDED.student_id,
                    class_id = EXCLUDED.class_id
                """
            ),
            {
                "id": enr_id,
                "lea": lea_id,
                "stu": stu_id,
                "cls": class_id,
                "begin": date(2026, 8, 15),
                "end": date(2027, 6, 12),
            },
        )


def _class_title_for(class_id: str) -> str:
    """Render a readable class title from the demo class id.

    The seed's class ids look like ``cls-lkw-101``; "Class 101" is
    the right granularity for an at-a-glance demo. Production data
    carries the real OneRoster ``title`` and is unaffected.
    """

    parts = class_id.split("-")
    return f"Class {parts[-1]}" if parts else class_id


async def _insert_sync_job(
    session: AsyncSession,
    *,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    status: str,
    started_at: datetime,
    event_count: int,
    cursor_before: str | None,
    cursor_after: str | None,
    error_summary: str | None = None,
    error_count: int = 0,
    warning_count: int = 0,
) -> None:
    completed_at = (
        started_at + timedelta(seconds=2) if status != "running" else None
    )
    await session.execute(
        text(
            """
            INSERT INTO sync_jobs (
                id, lea_id, partner, status, started_at, completed_at,
                event_count, error_count, warning_count,
                cursor_before, cursor_after, error_summary
            ) VALUES (
                :id, :lea, 'edlink', :status, :started_at, :completed_at,
                :event_count, :error_count, :warning_count,
                :cursor_before, :cursor_after, :error_summary
            )
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                started_at = EXCLUDED.started_at,
                completed_at = EXCLUDED.completed_at,
                event_count = EXCLUDED.event_count,
                error_count = EXCLUDED.error_count,
                warning_count = EXCLUDED.warning_count,
                cursor_before = EXCLUDED.cursor_before,
                cursor_after = EXCLUDED.cursor_after,
                error_summary = EXCLUDED.error_summary
            """
        ),
        {
            "id": sync_job_id,
            "lea": lea_id,
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "event_count": event_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "cursor_before": cursor_before,
            "cursor_after": cursor_after,
            "error_summary": error_summary,
        },
    )


async def _insert_validation_issue(
    session: AsyncSession,
    *,
    sync_job_id: uuid.UUID,
    layer: int,
    code: str,
    payload_reference: str | None,
    detail: dict[str, Any],
    created_at: datetime,
) -> None:
    issue_id = _seed_uuid("validation", f"{sync_job_id}:{layer}:{code}")
    await session.execute(
        text(
            """
            INSERT INTO sync_validation_results (
                id, sync_job_id, layer, code, payload_reference,
                detail, created_at
            ) VALUES (
                :id, :sj, :layer, :code, :ref, CAST(:detail AS JSONB), :now
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": issue_id,
            "sj": sync_job_id,
            "layer": layer,
            "code": code,
            "ref": payload_reference,
            "detail": json.dumps(detail),
            "now": created_at,
        },
    )


async def _insert_quarantine_row(
    session: AsyncSession,
    *,
    quarantine_id: uuid.UUID,
    sync_job_id: uuid.UUID,
    lea_id: LeaId,
    entity_type: str,
    entity_id: str,
    reason: str,
    raw_payload: dict[str, Any],
    created_at: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO quarantine (
                id, sync_job_id, lea_id, entity_type, entity_id, reason,
                raw_payload, created_at
            ) VALUES (
                :id, :sj, :lea, :etype, :eid, :reason,
                CAST(:payload AS JSONB), :now
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": quarantine_id,
            "sj": sync_job_id,
            "lea": lea_id,
            "etype": entity_type,
            "eid": entity_id,
            "reason": reason,
            "payload": json.dumps(raw_payload),
            "now": created_at,
        },
    )


async def _upsert_cursor(
    session: AsyncSession,
    *,
    lea_id: LeaId,
    last_event_id: str,
    last_event_at: datetime,
    last_poll_at: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO cursor_state (
                lea_id, partner, last_event_id, last_event_at,
                last_poll_at, cold_start_required, updated_at
            ) VALUES (
                :lea, 'edlink', :last_event_id, :last_event_at,
                :last_poll_at, false, :now
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
            "last_event_id": last_event_id,
            "last_event_at": last_event_at,
            "last_poll_at": last_poll_at,
            "now": datetime.now(UTC),
        },
    )


# ── Operators + connector authorizations ─────────────────────────────────────


async def _load_operator_ids_by_subject(
    session: AsyncSession,
) -> dict[str, uuid.UUID]:
    """Return the live `operator.id` for every seeded subject.

    The seed used to assume `operator.id` equalled
    `_seed_uuid("operator", subject)`. That breaks when the operator was
    created on-demand by `edlink_rostering.api.auth._load_or_create_operator`
    (which assigns `uuid.uuid4()`): the seed's role/grant inserts then
    reference an `operator_id` that does not exist, the rows error or
    point at nothing, and the persona authenticates with no active role.

    Reading the id back by subject after the upsert keeps the seed
    correct regardless of which path created the row first. Production
    operators arrive through an IdP/SCIM flow and have IDs the seed
    never controls; this same lookup model is what production needs.
    """

    subjects = [op.subject for op in SEEDED_OPERATORS]
    rows = (
        await session.execute(
            text(
                "SELECT subject, id FROM operator WHERE subject = ANY(:subs)"
            ),
            {"subs": subjects},
        )
    ).all()
    return {r.subject: r.id for r in rows}


async def _seed_operators(session: AsyncSession) -> None:
    """Insert the six operator personas + their role grants.

    Operator rows are upserted by subject. If a row already exists
    (e.g. created on-demand by the auth layer), the existing `id` is
    preserved by ON CONFLICT (subject) DO UPDATE; we then look up the
    actual ids by subject so role grants reference real rows.
    """

    for op in SEEDED_OPERATORS:
        op_id = _seed_uuid("operator", op.subject)
        await session.execute(
            text(
                """
                INSERT INTO operator (id, subject, display_name, email, status)
                VALUES (:id, :subject, :name, :email, 'active')
                ON CONFLICT (subject) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    email = EXCLUDED.email,
                    status = EXCLUDED.status
                """
            ),
            {
                "id": op_id,
                "subject": op.subject,
                "name": op.display_name,
                "email": op.email,
            },
        )

    op_ids = await _load_operator_ids_by_subject(session)

    # First owner self-references; everyone else is granted by
    # the bootstrap. Idempotent via deterministic role row id keyed on
    # (subject, role).
    bootstrap_subject = next(
        (op.subject for op in SEEDED_OPERATORS if op.role == "owner"),
        None,
    )
    assert bootstrap_subject is not None, "seed must include a owner"
    bootstrap_id = op_ids[bootstrap_subject]

    for op in SEEDED_OPERATORS:
        op_id = op_ids[op.subject]
        role_id = _seed_uuid("operator-role", f"{op.subject}:{op.role}")
        granted_by = op_id if op_id == bootstrap_id else bootstrap_id
        await session.execute(
            text(
                """
                INSERT INTO operator_role
                    (id, operator_id, role, granted_by, reason)
                VALUES (:id, :op, :role, :granted_by, :reason)
                ON CONFLICT (id) DO UPDATE SET
                    operator_id = EXCLUDED.operator_id,
                    role = EXCLUDED.role,
                    granted_by = EXCLUDED.granted_by,
                    reason = EXCLUDED.reason,
                    revoked_at = NULL
                """
            ),
            {
                "id": role_id,
                "op": op_id,
                "role": op.role,
                "granted_by": granted_by,
                "reason": "dev seed bootstrap",
            },
        )


async def _seed_connector_authorizations(
    session: AsyncSession, now: datetime
) -> None:
    """One active edlink authorization per seeded LEA.

    Plus the single-LEA operator personas' authorized LEAs are also
    inserted so the multi-tenancy enforcement tests have something to
    point at. `secret_ref` is a Key Vault name placeholder; the value
    itself never lives in Postgres.
    """

    op_ids = await _load_operator_ids_by_subject(session)
    authorized_by = op_ids["stephen-dev-001"]

    for lea in SEEDED_LEAS:
        authz_id = _seed_uuid("connector-authz", f"{lea.id}:edlink")
        await session.execute(
            text(
                """
                INSERT INTO connector_authorization
                    (id, lea_id, partner, status, authorized_at,
                     authorized_by, secret_ref, poll_interval_seconds,
                     notes)
                VALUES
                    (:id, :lea, 'edlink', 'active', :now,
                     :by, :secret, 300, :notes)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    authorized_at = EXCLUDED.authorized_at,
                    authorized_by = EXCLUDED.authorized_by,
                    secret_ref = EXCLUDED.secret_ref,
                    poll_interval_seconds = EXCLUDED.poll_interval_seconds,
                    notes = EXCLUDED.notes
                """
            ),
            {
                "id": authz_id,
                "lea": lea.id,
                "now": now,
                "by": authorized_by,
                "secret": f"edlink-token-{lea.id}",
                "notes": "dev seed; edlink mock connector",
            },
        )


async def _seed_operator_lea_grants(session: AsyncSession) -> None:
    """Grant single-LEA operator personas access to their LEA.

    The two single-LEA personas (lakewood-ops-001 and
    district-ops-001) get one ``operator_lea_grant`` row each that
    points at their LEA. The other personas have implicit
    organization-wide access via their role and are not in this
    table. The bootstrap founder (stephen-dev-001) authors every
    grant in the dev seed.
    """

    op_ids = await _load_operator_ids_by_subject(session)
    granted_by = op_ids["stephen-dev-001"]
    for op in SEEDED_OPERATORS:
        if op.role != "operator" or op.authorized_leas is None:
            continue
        operator_id = op_ids[op.subject]
        for lea in op.authorized_leas:
            grant_id = _seed_uuid(
                "operator-lea-grant", f"{op.subject}:{lea}"
            )
            await session.execute(
                text(
                    """
                    INSERT INTO operator_lea_grant
                        (id, operator_id, lea_id, granted_by, reason)
                    VALUES (:id, :op, :lea, :by, :reason)
                    ON CONFLICT (id) DO UPDATE SET
                        operator_id = EXCLUDED.operator_id,
                        granted_by = EXCLUDED.granted_by,
                        reason = EXCLUDED.reason,
                        revoked_at = NULL
                    """
                ),
                {
                    "id": grant_id,
                    "op": operator_id,
                    "lea": lea,
                    "by": granted_by,
                    "reason": "dev seed: single-LEA operator scope",
                },
            )


# ── Reconciliation runs ──────────────────────────────────────────────────────


async def _seed_reconciliation_runs(
    session: AsyncSession, now: datetime
) -> None:
    """Seed one matched + one drift_detected reconciliation_runs row.

    Lakewood is the happy-path LEA so a matched run there is the
    canonical example. Northridge carries a synthetic drift_detected run
    so the dashboard's audit-log explorer (future Phase 2) has an
    immediate example of drift handling. The rows are deterministic
    via UUID5 so reruns update timing fields rather than inserting
    duplicates.
    """

    lakewood_run_id = _seed_uuid("reconciliation", "lea-lakewood-usd:matched")
    await session.execute(
        text(
            """
            INSERT INTO reconciliation_runs (
                id, lea_id, partner, started_at, completed_at,
                status, canonical_root_hash, partner_root_hash,
                drift_summary, error_message
            ) VALUES (
                :id, 'lea-lakewood-usd', 'edlink',
                :started, :completed,
                'matched', :root, :root,
                NULL, NULL
            )
            ON CONFLICT (id) DO UPDATE SET
                started_at = EXCLUDED.started_at,
                completed_at = EXCLUDED.completed_at,
                status = EXCLUDED.status
            """
        ),
        {
            "id": lakewood_run_id,
            "started": now - timedelta(hours=22),
            "completed": now - timedelta(hours=22, minutes=-2),
            "root": "0" * 64,
        },
    )

    northridge_run_id = _seed_uuid("reconciliation", "lea-northridge-sd:drift")
    drift_summary = json.dumps(
        [
            {
                "entity_type": "enrollments",
                "canonical_only_ids": ["enr-nrd-canonical-only-001"],
                "partner_only_ids": ["enr-nrd-partner-only-001"],
                "canonical_mid_hash": "a" * 64,
                "partner_mid_hash": "b" * 64,
            }
        ]
    )
    await session.execute(
        text(
            """
            INSERT INTO reconciliation_runs (
                id, lea_id, partner, started_at, completed_at,
                status, canonical_root_hash, partner_root_hash,
                drift_summary, error_message
            ) VALUES (
                :id, 'lea-northridge-sd', 'edlink',
                :started, :completed,
                'drift_detected',
                :canonical, :partner,
                CAST(:drift AS JSONB), NULL
            )
            ON CONFLICT (id) DO UPDATE SET
                started_at = EXCLUDED.started_at,
                completed_at = EXCLUDED.completed_at,
                status = EXCLUDED.status
            """
        ),
        {
            "id": northridge_run_id,
            "started": now - timedelta(hours=10),
            "completed": now - timedelta(hours=10, minutes=-3),
            "canonical": "c" * 64,
            "partner": "d" * 64,
            "drift": drift_summary,
        },
    )


# ── Entry point ──────────────────────────────────────────────────────────────


def _build_factory() -> async_sessionmaker[AsyncSession]:
    import os

    url = (
        os.environ.get("OPS_DATABASE_URL")
        or os.environ.get("APP_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise RuntimeError(
            "Set DATABASE_URL (or OPS_DATABASE_URL) to a Postgres async URL."
        )
    engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def main() -> None:
    factory = _build_factory()
    # psycopg async refuses Windows' default ProactorEventLoop. Passing
    # SelectorEventLoop as loop_factory is the Python 3.12+ modern
    # replacement for the deprecated set_event_loop_policy /
    # WindowsSelectorEventLoopPolicy pair (both slated for removal in
    # 3.16). Cross-platform: SelectorEventLoop is the default on Unix
    # and the working override on Windows.
    asyncio.run(
        seed_realistic_state(factory),
        loop_factory=asyncio.SelectorEventLoop,
    )
    print(
        f"Seeded {len(SEEDED_LEAS)} LEAs: "
        + ", ".join(lea.id for lea in SEEDED_LEAS)
    )


if __name__ == "__main__":
    main()


__all__ = [
    "SEEDED_LEAS",
    "SEEDED_OPERATORS",
    "SeededLea",
    "SeededOperator",
    "seed_realistic_state",
]
