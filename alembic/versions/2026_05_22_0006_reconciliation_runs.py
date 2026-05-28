"""Reconciliation runs audit table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-22

Daily Merkle reconciliation lands per
``docs/design/edlink-oneroster-rostering.md`` § "Reconciliation." Each
pass writes one row carrying the canonical-side root hash, the
partner-side root hash, the status (matched, drift_detected, or
skipped_quiet_window), and a JSON column listing divergent entities
on drift.

Schema:

- ``id`` uuid PK
- ``lea_id`` FK to ``leas.id``
- ``partner`` text (the connector name; mirrors sync_jobs.partner)
- ``started_at`` / ``completed_at`` timestamptz
- ``status`` text check (in 'matched','drift_detected','skipped_quiet_window','failed')
- ``canonical_root_hash`` text (hex SHA-256)
- ``partner_root_hash`` text (hex SHA-256, nullable when skipped)
- ``drift_summary`` jsonb (per-entity-type mid-hash mismatches and
  affected entity ids; populated only when status='drift_detected')
- ``error_message`` text (populated when status='failed')

Grants follow the three-role pattern: ``edlink_app`` writes runs
(the reconciliation worker runs as the app role), ``edlink_ops``
reads, ``edlink_dba`` has full retention access. The append-only
discipline matches the other audit tables (no UPDATE / DELETE for the
app or ops roles).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "lea_id",
            sa.String(),
            sa.ForeignKey("leas.id"),
            nullable=False,
        ),
        sa.Column("partner", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("canonical_root_hash", sa.Text(), nullable=False),
        sa.Column("partner_root_hash", sa.Text(), nullable=True),
        sa.Column(
            "drift_summary", sa.dialects.postgresql.JSONB(), nullable=True
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('matched','drift_detected','skipped_quiet_window','failed')",
            name="ck_reconciliation_runs_status",
        ),
    )
    op.create_index(
        "ix_reconciliation_runs_lea_started",
        "reconciliation_runs",
        ["lea_id", "started_at"],
    )
    op.create_index(
        "ix_reconciliation_runs_status_started",
        "reconciliation_runs",
        ["status", "started_at"],
    )

    op.execute(
        """
        GRANT SELECT, INSERT ON reconciliation_runs TO edlink_app;
        GRANT SELECT ON reconciliation_runs TO edlink_ops;
        GRANT SELECT, INSERT, UPDATE, DELETE ON reconciliation_runs
            TO edlink_dba;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_reconciliation_runs_status_started;"
    )
    op.execute(
        "DROP INDEX IF EXISTS ix_reconciliation_runs_lea_started;"
    )
    op.drop_table("reconciliation_runs")
