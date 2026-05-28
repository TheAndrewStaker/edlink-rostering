"""Add source_event_id and source_event_at to snapshot tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20

The sync worker uses ``source_event_id`` as the per-natural-key high-water
mark for replay deduplication. When the same EdLink event is processed twice
(cursor rewind, operator-driven replay, redelivery after a Service Bus lock
release), the worker reads the latest live snapshot's ``source_event_id``
and skips the event if it is less than or equal to that value. This is what
makes "process the same page twice" produce zero new snapshot rows.

``source_event_at`` is the partner's ``created_date`` for the event, kept
alongside the ID so the snapshot timeline is queryable by source time
without joining back to a separate event log. For the EdLink Events API the
event IDs are monotonically ordered, but storing the timestamp is the
defensive choice for connectors whose event IDs are opaque.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SNAPSHOT_TABLES = ("lea_snapshots", "student_snapshots", "enrollment_snapshots")


def upgrade() -> None:
    for table in _SNAPSHOT_TABLES:
        op.add_column(
            table,
            sa.Column("source_event_id", sa.String(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(
                "source_event_at", sa.DateTime(timezone=True), nullable=True
            ),
        )
        op.create_index(
            f"ix_{table}_source_event_id", table, ["source_event_id"]
        )


def downgrade() -> None:
    for table in _SNAPSHOT_TABLES:
        op.drop_index(f"ix_{table}_source_event_id", table_name=table)
        op.drop_column(table, "source_event_at")
        op.drop_column(table, "source_event_id")
