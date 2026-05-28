"""LEA onboarding columns: status, timezone, edlink_integration_id.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-27

Backs the onboarding flow that creates an LEA in an "onboarding" status
before any roster events flow and graduates it to "active" after the
first successful sync. Adds three columns on ``leas``:

- ``status`` (``onboarding`` | ``active`` | ``decommissioned``,
  NOT NULL DEFAULT 'onboarding'). Indexed so the dashboard list query
  can filter by status without a sequential scan once the LEA fleet
  grows.
- ``timezone`` (NOT NULL DEFAULT 'America/New_York'). Region defaults
  matter for compliance windows and IDEA timeline math; the per-LEA
  value lets the rostering layer hand the right offset to downstream
  services without guessing from the state code.
- ``edlink_integration_id`` (nullable, unique). EdLink assigns each
  district its own integration id; the onboarding CLI stores it on
  the LEA so the connector framework can look up the right EdLink
  context without a side-channel lookup. UNIQUE so two LEAs cannot
  accidentally share an EdLink integration.

The defaults backfill existing rows. ``status`` defaults to
``onboarding`` for the legacy seeded LEAs so that operators have to
explicitly activate them through the onboarding flow rather than
inherit an "active" status without proof.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "leas",
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'onboarding'"),
        ),
    )
    op.add_column(
        "leas",
        sa.Column(
            "timezone",
            sa.String(),
            nullable=False,
            server_default=sa.text("'America/New_York'"),
        ),
    )
    op.add_column(
        "leas",
        sa.Column(
            "edlink_integration_id",
            sa.String(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_leas_status",
        "leas",
        ["status"],
    )
    op.create_unique_constraint(
        "uq_leas_edlink_integration_id",
        "leas",
        ["edlink_integration_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_leas_edlink_integration_id",
        "leas",
        type_="unique",
    )
    op.drop_index("ix_leas_status", table_name="leas")
    op.drop_column("leas", "edlink_integration_id")
    op.drop_column("leas", "timezone")
    op.drop_column("leas", "status")
