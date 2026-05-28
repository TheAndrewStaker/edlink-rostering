"""Reconciliation breadth: schools/classes/academic_sessions; integration status.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-28

Two-part migration. The two parts ship together because both back the
session-B reconciliation hardening: the resource walk now covers all
five OneRoster entity families, and each sync polls EdLink's
per-integration status so degraded integrations surface in the admin
app.

Part one: three canonical tables (``schools``, ``classes``,
``academic_sessions``) so the reconciliation Merkle hash can cover
the full OneRoster resource set EdLink exposes. The connector's
``walk_resources`` already projects rows for these types; the missing
piece was a canonical-side table for the leaf hash to compare
against. Each table carries ``lea_id`` as the tenant column with the
matching FK to ``leas(id)`` per ``.claude/rules/multi-tenancy.md``,
the same composite-unique-constraint pattern used by ``students``
(``UniqueConstraint(lea_id, id)``) so child tables can compose a
composite FK against ``(lea_id, id)`` later without a schema
migration. Soft-delete via ``deleted_at`` matches the existing
canonical convention so reconciliation queries can keep filtering
``deleted_at IS NULL`` across all five entity-types uniformly.

Part two: two columns on ``connector_authorization``,
``integration_status`` (the EdLink-side enum: ``inactive``,
``active``, ``requested``, ``disabled``, ``destroyed``) and
``sharing_scope`` (the OneRoster sharing scope EdLink reports per
integration: ``full``, ``rostering_only``, ``read_only``,
``revoked``). The sync worker writes these on every poll;
``integration_status`` defaults to ``active`` so existing rows are
not silently degraded by the migration alone.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INTEGRATION_STATUS_VALUES = (
    "inactive",
    "active",
    "requested",
    "disabled",
    "destroyed",
)


def upgrade() -> None:
    # ── schools ──────────────────────────────────────────────────────────────
    op.create_table(
        "schools",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "lea_id",
            sa.String(),
            sa.ForeignKey("leas.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("school_code", sa.String(), nullable=True),
        sa.Column("parent_org_id", sa.String(), nullable=True),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint("lea_id", "id", name="uq_schools_lea_id"),
    )
    op.create_index("ix_schools_lea_id", "schools", ["lea_id"])

    # ── academic_sessions ────────────────────────────────────────────────────
    op.create_table(
        "academic_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "lea_id",
            sa.String(),
            sa.ForeignKey("leas.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("session_type", sa.String(), nullable=True),
        sa.Column("school_year", sa.String(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "lea_id", "id", name="uq_academic_sessions_lea_id"
        ),
    )
    op.create_index(
        "ix_academic_sessions_lea_id", "academic_sessions", ["lea_id"]
    )

    # ── classes ──────────────────────────────────────────────────────────────
    op.create_table(
        "classes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "lea_id",
            sa.String(),
            sa.ForeignKey("leas.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("course_code", sa.String(), nullable=True),
        sa.Column("school_id", sa.String(), nullable=True),
        sa.Column("term_id", sa.String(), nullable=True),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint("lea_id", "id", name="uq_classes_lea_id"),
    )
    op.create_index("ix_classes_lea_id", "classes", ["lea_id"])

    # ── connector_authorization columns ──────────────────────────────────────
    op.add_column(
        "connector_authorization",
        sa.Column(
            "integration_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.add_column(
        "connector_authorization",
        sa.Column(
            "sharing_scope",
            sa.Text(),
            nullable=True,
        ),
    )
    op.add_column(
        "connector_authorization",
        sa.Column(
            "integration_status_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_connector_authorization_integration_status",
        "connector_authorization",
        "integration_status IN "
        "('inactive','active','requested','disabled','destroyed')",
    )

    # ── Grants ───────────────────────────────────────────────────────────────
    # New canonical tables follow the V0001 three-role pattern:
    # edlink_app reads + writes (the sync worker upserts these), the
    # operator surface reads them only, edlink_dba retains retention
    # access. The grant SQL is idempotent in Postgres because GRANT
    # is naturally idempotent.
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON
            schools, classes, academic_sessions
        TO edlink_app;
        GRANT SELECT ON
            schools, classes, academic_sessions
        TO edlink_ops;
        GRANT SELECT, INSERT, UPDATE, DELETE ON
            schools, classes, academic_sessions
        TO edlink_dba;
        """
    )
    op.execute(
        """
        GRANT UPDATE (
            integration_status,
            sharing_scope,
            integration_status_observed_at
        ) ON connector_authorization TO edlink_app;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE UPDATE (
            integration_status,
            sharing_scope,
            integration_status_observed_at
        ) ON connector_authorization FROM edlink_app;
        """
    )
    op.drop_constraint(
        "ck_connector_authorization_integration_status",
        "connector_authorization",
        type_="check",
    )
    op.drop_column(
        "connector_authorization", "integration_status_observed_at"
    )
    op.drop_column("connector_authorization", "sharing_scope")
    op.drop_column("connector_authorization", "integration_status")

    op.drop_index("ix_classes_lea_id", table_name="classes")
    op.drop_table("classes")

    op.drop_index(
        "ix_academic_sessions_lea_id", table_name="academic_sessions"
    )
    op.drop_table("academic_sessions")

    op.drop_index("ix_schools_lea_id", table_name="schools")
    op.drop_table("schools")
