"""Rename roles: founder_admin -> owner, connector_admin -> admin.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-28

The original V0004 schema named the two organization-wide roles
``founder_admin`` and ``connector_admin``. Both names presumed an
org structure that does not generalize: districts adopting this app
do not have founders, and the "connector" word leaked the framework
primitive into the role label. Re-naming to ``owner`` / ``admin``
matches the de-facto SaaS naming convention (GitHub / Linear /
Vercel / Notion) and Workato's role taxonomy (the closest
integration-platform peer; they ship Admin / Analyst / Operator and
we extend with an Owner tier for grant-management).

The four-role shape after this migration:

- ``owner`` (was ``founder_admin``): grant-management tier; the
  role that grants other roles, manages billing, deletes the
  workspace. Implicit org-wide LEA scope.
- ``admin`` (was ``connector_admin``): day-to-day platform admin;
  authorize / revoke / rotate / adjust integrations + LEA create
  + status transitions. Implicit org-wide LEA scope.
- ``operator`` (unchanged): district-scoped reads via
  ``operator_lea_grant``; no mutations.
- ``auditor`` (unchanged): read-only across all LEAs; FERPA
  evidence trail.

Mechanics:

1. Drop the V0004 ``ck_operator_role_value`` check constraint so
   the ``UPDATE`` pass below can rename rows without violating the
   old enum.
2. Rename existing rows in ``operator_role`` from the old names to
   the new ones. Both fields ``role`` (the value) are renamed in
   place; row IDs and grant/revoke timestamps are preserved so the
   audit history stays intact.
3. Re-create the check constraint with the new enum set.

The check constraint name (``ck_operator_role_value``) is unchanged
so downstream tooling that queries pg_constraint by name keeps
working. The constraint definition is what changes.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE operator_role DROP CONSTRAINT IF EXISTS"
        " ck_operator_role_value"
    )
    op.execute(
        "UPDATE operator_role SET role = 'owner'"
        " WHERE role = 'founder_admin'"
    )
    op.execute(
        "UPDATE operator_role SET role = 'admin'"
        " WHERE role = 'connector_admin'"
    )
    op.execute(
        "ALTER TABLE operator_role ADD CONSTRAINT"
        " ck_operator_role_value CHECK"
        " (role IN ('operator','admin','owner','auditor'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE operator_role DROP CONSTRAINT IF EXISTS"
        " ck_operator_role_value"
    )
    op.execute(
        "UPDATE operator_role SET role = 'founder_admin'"
        " WHERE role = 'owner'"
    )
    op.execute(
        "UPDATE operator_role SET role = 'connector_admin'"
        " WHERE role = 'admin'"
    )
    op.execute(
        "ALTER TABLE operator_role ADD CONSTRAINT"
        " ck_operator_role_value CHECK"
        " (role IN ('operator','connector_admin','founder_admin','auditor'))"
    )
