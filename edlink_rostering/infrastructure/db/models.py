"""SQLAlchemy ORM models for the EdLink rostering POC.

Three table families:

1. **Canonical** (`leas`, `students`, `enrollments`): the current view. One row
   per entity. Upserted on each sync. Soft-deleted via `deleted_at`.

2. **Snapshots** (`lea_snapshots`, `student_snapshots`, `enrollment_snapshots`):
   append-only history. `generation_id` is the `sync_jobs.id` that wrote the
   row. `superseded_by_generation_id` and `superseded_at` are set when a later
   sync writes a new snapshot for the same natural key. `deleted_upstream` is
   true when the partner emitted a deletion event. Revert clears
   `superseded_by_generation_id` on prior snapshots to restore them.

3. **Audit and operational** (`sync_jobs`, `sync_validation_results`,
   `revert_actions`, `quarantine`, `cursor_state`): the operator-facing record
   of what happened, why, and where the cursor is.

Schema is created and managed by Alembic. These mapped classes are runtime
read/write types only. Per `docs/design/edlink-oneroster-rostering.md`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Common base; SQLAlchemy 2.x DeclarativeBase pattern."""


# ── Canonical ─────────────────────────────────────────────────────────────────


class Lea(Base):
    __tablename__ = "leas"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    lea_type: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    nces_lea_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, index=True, default="onboarding"
    )
    timezone: Mapped[str] = mapped_column(
        String, nullable=False, default="America/New_York"
    )
    edlink_integration_id: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Student(Base):
    __tablename__ = "students"
    # ADR-006: composite uniqueness so (lea_id, id) can be the target of the
    # enrollments composite FK. id alone remains the primary key.
    __table_args__ = (
        UniqueConstraint("lea_id", "id", name="uq_students_lea_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    lea_id: Mapped[str] = mapped_column(String, ForeignKey("leas.id"), nullable=False, index=True)
    given_name: Mapped[str] = mapped_column(String, nullable=False)
    family_name: Mapped[str] = mapped_column(String, nullable=False)
    grade: Mapped[str | None] = mapped_column(String, nullable=True)
    preferred_first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_school_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Enrollment(Base):
    __tablename__ = "enrollments"
    # ADR-006: composite FK on (lea_id, student_id) makes cross-LEA refs
    # physically impossible, not just discouraged. The single-column FK on
    # lea_id stays for the leas.id reference.
    __table_args__ = (
        ForeignKeyConstraint(
            ["lea_id", "student_id"],
            ["students.lea_id", "students.id"],
            name="fk_enrollments_lea_student",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    lea_id: Mapped[str] = mapped_column(String, ForeignKey("leas.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    class_id: Mapped[str] = mapped_column(String, nullable=False)
    begin_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class School(Base):
    __tablename__ = "schools"
    __table_args__ = (
        UniqueConstraint("lea_id", "id", name="uq_schools_lea_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    lea_id: Mapped[str] = mapped_column(
        String, ForeignKey("leas.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    school_code: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_org_id: Mapped[str | None] = mapped_column(String, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AcademicSession(Base):
    __tablename__ = "academic_sessions"
    __table_args__ = (
        UniqueConstraint(
            "lea_id", "id", name="uq_academic_sessions_lea_id"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    lea_id: Mapped[str] = mapped_column(
        String, ForeignKey("leas.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    session_type: Mapped[str | None] = mapped_column(String, nullable=True)
    school_year: Mapped[str | None] = mapped_column(String, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Class(Base):
    __tablename__ = "classes"
    __table_args__ = (
        UniqueConstraint("lea_id", "id", name="uq_classes_lea_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    lea_id: Mapped[str] = mapped_column(
        String, ForeignKey("leas.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    course_code: Mapped[str | None] = mapped_column(String, nullable=True)
    school_id: Mapped[str | None] = mapped_column(String, nullable=True)
    term_id: Mapped[str | None] = mapped_column(String, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ── Snapshots (append-only) ──────────────────────────────────────────────────


class LeaSnapshot(Base):
    __tablename__ = "lea_snapshots"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lea_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    generation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sync_jobs.id"), nullable=False, index=True)
    superseded_by_generation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sync_jobs.id"), nullable=True, index=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_upstream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_event_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class StudentSnapshot(Base):
    __tablename__ = "student_snapshots"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    lea_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    generation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sync_jobs.id"), nullable=False, index=True)
    superseded_by_generation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sync_jobs.id"), nullable=True, index=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_upstream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_event_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class EnrollmentSnapshot(Base):
    __tablename__ = "enrollment_snapshots"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    enrollment_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    lea_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    generation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sync_jobs.id"), nullable=False, index=True)
    superseded_by_generation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sync_jobs.id"), nullable=True, index=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_upstream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_event_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


# ── Audit ─────────────────────────────────────────────────────────────────────


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lea_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    partner: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor_before: Mapped[str | None] = mapped_column(String, nullable=True)
    cursor_after: Mapped[str | None] = mapped_column(String, nullable=True)
    partner_checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    canonical_checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    checksum_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncValidationResult(Base):
    __tablename__ = "sync_validation_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sync_jobs.id"), nullable=False, index=True)
    layer: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    payload_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RevertAction(Base):
    __tablename__ = "revert_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sync_jobs.id"), nullable=False, index=True)
    revert_generation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operator_identity: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reverted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshots_restored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Quarantine(Base):
    __tablename__ = "quarantine"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sync_jobs.id"), nullable=False, index=True)
    lea_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_status: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution_operator: Mapped[str | None] = mapped_column(String, nullable=True)


# ── Operational ───────────────────────────────────────────────────────────────


class CursorState(Base):
    """One row per (lea_id, partner). Tracks where incremental polling is."""

    __tablename__ = "cursor_state"

    lea_id: Mapped[str] = mapped_column(String, primary_key=True)
    partner: Mapped[str] = mapped_column(String, primary_key=True)
    last_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cold_start_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
