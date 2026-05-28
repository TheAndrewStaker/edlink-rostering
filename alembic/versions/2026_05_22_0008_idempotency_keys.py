"""Idempotency-Key replay table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-22

Backs the ``Idempotency-Key`` header contract on mutation endpoints.
When an operator sends ``Idempotency-Key: <uuid>`` with a mutation
(retry, revert, connector lifecycle, quarantine release/reject), the
API stores a row keyed on ``(operator_id, route, key)``. A second
request with the same key returns the cached response instead of
executing the mutation a second time.

Rows are written in two stages:

1. **Pending insert** before the handler runs. ``request_hash`` is
   filled, ``response_status`` and ``response_body`` are NULL.
2. **Completed update** after the handler returns. ``response_status``
   and ``response_body`` are populated; ``completed_at`` is set.

A request that finds a pending row for the same key gets a 409 (an
earlier in-flight request is still running). A request that finds a
completed row with a *different* ``request_hash`` gets a 422
(Stripe-style strict mode: same key + different body = client bug).

A background sweep deletes rows older than 24 hours; the
``created_at`` index supports the sweep without a full scan.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column(
            "response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "operator_id", "route", "key", name="pk_idempotency_keys"
        ),
    )
    op.create_index(
        "ix_idempotency_keys_created_at",
        "idempotency_keys",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_idempotency_keys_created_at",
        table_name="idempotency_keys",
    )
    op.drop_table("idempotency_keys")
