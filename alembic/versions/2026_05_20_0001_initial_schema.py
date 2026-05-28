"""Initial schema: canonical, snapshots, audit, operational, plus three roles.

Revision ID: 0001
Revises:
Create Date: 2026-05-20

Schema for the EdLink rostering POC per
`docs/design/edlink-oneroster-rostering.md`. Three table families plus three
Postgres roles. Designed to run idempotently against a fresh Postgres database;
re-running on an existing schema is a no-op for role creation but will fail on
table creation (Alembic version table handles the table case).

Role privileges:

- `edlink_app` (sync worker): INSERT and SELECT on canonical and audit
  tables. UPDATE only on snapshot supersession columns, canonical entity
  fields, and `cursor_state`. No DELETE anywhere.
- `edlink_ops` (operator CLI): SELECT everywhere. INSERT on `revert_actions`
  and `quarantine` resolution columns. UPDATE on snapshots (for revert clearing
  `superseded_by_generation_id`) and on `cursor_state` (for cursor reset).
- `edlink_dba` (retention, break-glass): full UPDATE/DELETE on audit. Used by
  retention jobs and emergency intervention.

Column-level supersession-only UPDATE is enforced at the application layer in
session 1. A trigger that constrains UPDATE to only the supersession columns
lands in session 2.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Roles (idempotent) ────────────────────────────────────────────────
    # CREATE ROLE has no IF NOT EXISTS; wrap in a DO block. Passwords are not
    # set here; production provisioning sets per-environment passwords out of
    # band. Local development uses peer or trust auth.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'edlink_app') THEN
                CREATE ROLE edlink_app LOGIN;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'edlink_ops') THEN
                CREATE ROLE edlink_ops LOGIN;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'edlink_dba') THEN
                CREATE ROLE edlink_dba LOGIN;
            END IF;
        END
        $$;
        """
    )

    # ── Canonical ─────────────────────────────────────────────────────────
    op.create_table(
        "leas",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("lea_type", sa.String(), nullable=False),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("nces_lea_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "students",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("lea_id", sa.String(), sa.ForeignKey("leas.id"), nullable=False),
        sa.Column("given_name", sa.String(), nullable=False),
        sa.Column("family_name", sa.String(), nullable=False),
        sa.Column("grade", sa.String(), nullable=True),
        sa.Column("preferred_first_name", sa.String(), nullable=True),
        sa.Column("primary_school_id", sa.String(), nullable=True),
        sa.Column(
            "external_ids",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_students_lea_id", "students", ["lea_id"])

    op.create_table(
        "enrollments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("lea_id", sa.String(), sa.ForeignKey("leas.id"), nullable=False),
        sa.Column("student_id", sa.String(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("class_id", sa.String(), nullable=False),
        sa.Column("begin_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_enrollments_lea_id", "enrollments", ["lea_id"])
    op.create_index("ix_enrollments_student_id", "enrollments", ["student_id"])

    # ── Audit (created before snapshots so FKs to sync_jobs.id resolve) ───
    op.create_table(
        "sync_jobs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("lea_id", sa.String(), nullable=False),
        sa.Column("partner", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cursor_before", sa.String(), nullable=True),
        sa.Column("cursor_after", sa.String(), nullable=True),
        sa.Column("partner_checksum", sa.String(), nullable=True),
        sa.Column("canonical_checksum", sa.String(), nullable=True),
        sa.Column("checksum_match", sa.Boolean(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_sync_jobs_lea_id", "sync_jobs", ["lea_id"])
    op.create_index("ix_sync_jobs_status", "sync_jobs", ["status"])

    op.create_table(
        "sync_validation_results",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "sync_job_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sync_jobs.id"),
            nullable=False,
        ),
        sa.Column("layer", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("payload_reference", sa.String(), nullable=True),
        sa.Column("detail", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_sync_validation_results_sync_job_id",
        "sync_validation_results",
        ["sync_job_id"],
    )

    op.create_table(
        "revert_actions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "sync_job_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sync_jobs.id"),
            nullable=False,
        ),
        sa.Column(
            "revert_generation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("operator_identity", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshots_restored", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_revert_actions_sync_job_id", "revert_actions", ["sync_job_id"])

    op.create_table(
        "quarantine",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "sync_job_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sync_jobs.id"),
            nullable=False,
        ),
        sa.Column("lea_id", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw_payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_status", sa.String(), nullable=True),
        sa.Column("resolution_operator", sa.String(), nullable=True),
    )
    op.create_index("ix_quarantine_lea_id", "quarantine", ["lea_id"])
    op.create_index("ix_quarantine_sync_job_id", "quarantine", ["sync_job_id"])

    # ── Snapshots (append-only, FK to sync_jobs) ──────────────────────────
    # For LEA snapshots the natural key IS lea_id, so the tenant column is the
    # same column. Student and Enrollment snapshots carry both their own
    # natural key and the lea_id tenant scope.
    for entity, fk_col in (
        ("lea", "lea_id"),
        ("student", "student_id"),
        ("enrollment", "enrollment_id"),
    ):
        columns: list[sa.Column[sa.types.TypeEngine[object]]] = [
            sa.Column(
                "snapshot_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(fk_col, sa.String(), nullable=False),
        ]
        if fk_col != "lea_id":
            columns.append(sa.Column("lea_id", sa.String(), nullable=False))
        columns.extend([
            sa.Column(
                "generation_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sync_jobs.id"),
                nullable=False,
            ),
            sa.Column(
                "superseded_by_generation_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sync_jobs.id"),
                nullable=True,
            ),
            sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "deleted_upstream",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        ])
        op.create_table(f"{entity}_snapshots", *columns)
        op.create_index(
            f"ix_{entity}_snapshots_{fk_col}", f"{entity}_snapshots", [fk_col]
        )
        if fk_col != "lea_id":
            op.create_index(
                f"ix_{entity}_snapshots_lea_id", f"{entity}_snapshots", ["lea_id"]
            )
        op.create_index(
            f"ix_{entity}_snapshots_generation_id",
            f"{entity}_snapshots",
            ["generation_id"],
        )
        op.create_index(
            f"ix_{entity}_snapshots_superseded_by",
            f"{entity}_snapshots",
            ["superseded_by_generation_id"],
        )

    # ── Operational ───────────────────────────────────────────────────────
    op.create_table(
        "cursor_state",
        sa.Column("lea_id", sa.String(), primary_key=True),
        sa.Column("partner", sa.String(), primary_key=True),
        sa.Column("last_event_id", sa.String(), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cold_start_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── Grants ────────────────────────────────────────────────────────────
    # edlink_app: sync worker. INSERT + SELECT on everything; UPDATE on
    # snapshots (for supersession), canonical, cursor_state. No DELETE.
    op.execute(
        """
        GRANT SELECT, INSERT ON
            leas, students, enrollments,
            lea_snapshots, student_snapshots, enrollment_snapshots,
            sync_jobs, sync_validation_results, quarantine, cursor_state
        TO edlink_app;

        GRANT UPDATE ON
            leas, students, enrollments,
            lea_snapshots, student_snapshots, enrollment_snapshots,
            cursor_state
        TO edlink_app;
        """
    )

    # edlink_ops: operator CLI. SELECT everywhere, INSERT for audit, UPDATE
    # snapshots (revert clears superseded_by_generation_id), UPDATE cursor.
    op.execute(
        """
        GRANT SELECT ON
            leas, students, enrollments,
            lea_snapshots, student_snapshots, enrollment_snapshots,
            sync_jobs, sync_validation_results, revert_actions, quarantine, cursor_state
        TO edlink_ops;

        GRANT INSERT ON revert_actions, quarantine TO edlink_ops;

        GRANT UPDATE ON
            lea_snapshots, student_snapshots, enrollment_snapshots,
            quarantine, cursor_state
        TO edlink_ops;
        """
    )

    # edlink_dba: retention and break-glass. Full UPDATE and DELETE on audit.
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON
            leas, students, enrollments,
            lea_snapshots, student_snapshots, enrollment_snapshots,
            sync_jobs, sync_validation_results, revert_actions, quarantine, cursor_state
        TO edlink_dba;
        """
    )

    # gen_random_uuid() is provided by pgcrypto in older Postgres; Postgres 13+
    # ships it in pg_catalog. CREATE EXTENSION is a no-op on 13+; left for
    # backwards compatibility.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")


def downgrade() -> None:
    op.drop_table("cursor_state")
    op.drop_table("enrollment_snapshots")
    op.drop_table("student_snapshots")
    op.drop_table("lea_snapshots")
    op.drop_table("quarantine")
    op.drop_table("revert_actions")
    op.drop_table("sync_validation_results")
    op.drop_table("sync_jobs")
    op.drop_table("enrollments")
    op.drop_table("students")
    op.drop_table("leas")
    # Roles are kept on downgrade; dropping roles requires reassigning
    # ownership of any objects they hold, which is a manual step.
