"""Snapshot UPDATE trigger + retry_actions table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21

Two changes:

1. ``retry_actions`` audit table. Parallel to ``revert_actions``: one row
   per operator-initiated retry of a failed sync. Captures the operator,
   the reason, and the cursor value the retry rewound the LEA to.

2. ``enforce_snapshot_immutability`` trigger function plus ``BEFORE UPDATE``
   triggers on the three snapshot tables. Application code only ever
   needs to change ``superseded_by_generation_id`` and ``superseded_at``
   on an existing snapshot row. The trigger compares the JSONB
   representation of NEW vs OLD with those two columns stripped; any
   other field difference raises. This makes the append-only contract
   from ``.claude/rules/temporal-model.md`` enforceable at the database
   level rather than relying on application discipline.

The trigger uses ``to_jsonb(row) - 'col_a' - 'col_b'`` so it works
identically on all three snapshot tables, including ``lea_snapshots``
whose natural key column differs from the other two.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SNAPSHOT_TABLES = ("lea_snapshots", "student_snapshots", "enrollment_snapshots")


def upgrade() -> None:
    op.create_table(
        "retry_actions",
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
        sa.Column("partner", sa.String(), nullable=False),
        sa.Column("operator_identity", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("retried_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor_rewound_to", sa.String(), nullable=True),
        sa.Column(
            "forced",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_retry_actions_sync_job_id", "retry_actions", ["sync_job_id"]
    )
    op.create_index("ix_retry_actions_lea_id", "retry_actions", ["lea_id"])

    op.execute(
        """
        GRANT SELECT, INSERT ON retry_actions TO edlink_ops;
        GRANT SELECT ON retry_actions TO edlink_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON retry_actions TO edlink_dba;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_snapshot_immutability()
        RETURNS TRIGGER AS $$
        BEGIN
            IF (to_jsonb(NEW)
                 - 'superseded_by_generation_id'
                 - 'superseded_at')
               IS DISTINCT FROM
               (to_jsonb(OLD)
                 - 'superseded_by_generation_id'
                 - 'superseded_at')
            THEN
                RAISE EXCEPTION
                    'snapshot rows only allow UPDATE of '
                    'superseded_by_generation_id and superseded_at '
                    '(attempted to change another column on %)',
                    TG_TABLE_NAME;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    for table in _SNAPSHOT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER tg_{table}_immutable
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION enforce_snapshot_immutability();
            """
        )


def downgrade() -> None:
    for table in _SNAPSHOT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS tg_{table}_immutable ON {table};")
    op.execute(
        "DROP FUNCTION IF EXISTS enforce_snapshot_immutability();"
    )
    op.drop_index("ix_retry_actions_lea_id", table_name="retry_actions")
    op.drop_index("ix_retry_actions_sync_job_id", table_name="retry_actions")
    op.drop_table("retry_actions")
