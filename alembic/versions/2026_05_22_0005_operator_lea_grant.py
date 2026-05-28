"""Per-operator LEA scope.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-22

V0004 introduced the role tables but left per-operator-LEA scoping
unresolved. The role matrix in ``docs/design/admin-surfaces.md`` says
``operator`` gets full operator-dashboard access; the Session 4 plan
adds explicit-grant scoping so single-LEA operator personas can be
tested against the multi-tenancy enforcement on action endpoints
(``test_operator_authorized_for_lea_a_cannot_retry_lea_b_sync`` and
friends).

The table is append-only history. A revoke writes ``revoked_at`` and
``revoked_by``; a re-grant inserts a fresh row. The partial unique
index on ``(operator_id, lea_id) WHERE revoked_at IS NULL`` enforces
"at most one active grant per (operator, LEA)" at the DB level.

Role semantics for the auth module:

- ``founder_admin``, ``connector_admin``, ``auditor``: implicit access
  to every active LEA. This table is not consulted for them.
- ``operator``: explicit only. The auth module reads
  ``operator_lea_grant`` and intersects with the active LEA set.

Grants follow the existing three-role pattern: ``edlink_app`` reads
(the auth-time lookup), ``edlink_ops`` reads + inserts + updates the
two supersession columns (``revoked_at``, ``revoked_by``),
``edlink_dba`` has full access.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operator_lea_grant",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "operator_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operator.id"),
            nullable=False,
        ),
        sa.Column(
            "lea_id",
            sa.String(),
            sa.ForeignKey("leas.id"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "granted_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operator.id"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operator.id"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_operator_lea_grant_operator_id",
        "operator_lea_grant",
        ["operator_id"],
    )
    op.create_index(
        "ix_operator_lea_grant_lea_id",
        "operator_lea_grant",
        ["lea_id"],
    )
    # At most one active grant per (operator, LEA). Re-granting after
    # a revoke writes a new row whose partial-index key collides only
    # with another active grant, not with the revoked history.
    op.execute(
        "CREATE UNIQUE INDEX uq_operator_lea_grant_active "
        "ON operator_lea_grant (operator_id, lea_id) "
        "WHERE revoked_at IS NULL;"
    )

    op.execute(
        """
        GRANT SELECT ON operator_lea_grant TO edlink_app;
        GRANT SELECT, INSERT ON operator_lea_grant TO edlink_ops;
        GRANT UPDATE (revoked_at, revoked_by)
            ON operator_lea_grant TO edlink_ops;
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON operator_lea_grant TO edlink_dba;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_operator_lea_grant_active;")
    op.execute("DROP INDEX IF EXISTS ix_operator_lea_grant_lea_id;")
    op.execute("DROP INDEX IF EXISTS ix_operator_lea_grant_operator_id;")
    op.drop_table("operator_lea_grant")
