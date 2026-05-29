"""drop connector_authorization.secret_ref

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-29

The ``secret_ref`` column modelled the EdLink per-LEA bearer token as a
Key Vault secret name that an operator staged, named, and rotated. That
contradicts how EdLink actually works: EdLink owns each district's
access token and exposes it on the integration object, addressed by the
stable ``edlink_integration_id`` (already stored on ``leas`` since
V0009). The connector authenticates via a deterministic
``edlink-token-<lea_id>`` Key Vault name, never via this column, so
``secret_ref`` was a misleading operator artifact rather than a
load-bearing field.

This migration drops the column. The Integrations surface now reads the
EdLink handle from ``leas.edlink_integration_id``; the per-LEA
"rotate credential" operator flow is removed in the same change set.
Column-level grants on ``secret_ref`` are dropped automatically by
Postgres when the column goes.

Irreversible data note: ``downgrade()`` re-creates the column with its
original NOT NULL shape (backfilling existing rows with an empty string
via a transient server default, then dropping the default to match the
V0004 definition) and restores the ``edlink_ops`` column grant. The
prior secret-name values are not recoverable; they were never the
source of truth for authentication.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("connector_authorization", "secret_ref")


def downgrade() -> None:
    op.add_column(
        "connector_authorization",
        sa.Column(
            "secret_ref",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.alter_column(
        "connector_authorization", "secret_ref", server_default=None
    )
    op.execute(
        "GRANT UPDATE (secret_ref) ON connector_authorization TO edlink_ops;"
    )
