"""Operator identity, role grants, connector authorization, audit log.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-22

Four tables that move the admin app off the mock ``X-Operator-Identity``
header onto a real auth seam. Schema follows
``docs/design/admin-surfaces.md`` § "Data model".

1. ``operator`` — one row per human who can sign into the admin app.
   ``subject`` is the IdP ``sub`` claim, stable across email changes.
   Email uniqueness is case-insensitive via a functional unique index
   on ``lower(email)`` so the schema does not depend on the optional
   ``citext`` extension.

2. ``operator_role`` — one row per role grant. A partial unique index
   on ``operator_id WHERE revoked_at IS NULL`` enforces "at most one
   active role per operator" at the database level. Role changes write
   a new row and revoke the prior; the table is append-only history.

3. ``connector_authorization`` — per ``(lea_id, partner)`` authorization
   state. ``secret_ref`` carries the Key Vault secret name; the value
   itself never lives in Postgres.

4. ``audit_log`` — non-sync operator actions (role changes,
   authorization changes, founder admin actions). Sync-side audit
   stays in ``sync_jobs``/``revert_actions``/``retry_actions``/
   ``quarantine``; the audit-log explorer UNIONs them at read time.

Grants follow the three-role pattern from V0001:

- ``edlink_app`` (sync worker + API): SELECT on the auth-read tables
  (``operator``, ``operator_role``, ``connector_authorization``) so
  the JWT validator can load the operator + authorized LEAs on every
  request. No INSERT — the worker never mutates auth state.
- ``edlink_ops`` (operator CLI + admin app): SELECT + INSERT on all
  four tables. Column-level UPDATE on the supersession columns
  (``operator.last_seen_at``, ``operator_role.revoked_at``,
  ``operator_role.revoked_by``, ``connector_authorization.status``
  and friends) so role-revoke and connector-revoke flows work without
  granting full UPDATE.
- ``edlink_dba``: full. Retention + break-glass path.

Bootstrap rows for the founder team are written from environment
variables (``BOOTSTRAP_OPERATOR_SUBJECTS``,
``BOOTSTRAP_OPERATOR_EMAILS``, ``BOOTSTRAP_OPERATOR_NAMES``). When
unset (test envs, CI without secrets) the bootstrap step is a no-op
and the dev seed inserts a parallel set of test operators via the
seed module instead.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ROLE_VALUES = ("operator", "connector_admin", "founder_admin", "auditor")
_OPERATOR_STATUS_VALUES = ("active", "disabled", "locked")
_AUTHZ_STATUS_VALUES = ("pending", "active", "revoked", "locked")


def upgrade() -> None:
    # ── operator ──────────────────────────────────────────────────────────
    op.create_table(
        "operator",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','disabled','locked')",
            name="ck_operator_status",
        ),
        sa.UniqueConstraint("subject", name="uq_operator_subject"),
    )
    # Case-insensitive email uniqueness without depending on citext.
    op.execute(
        "CREATE UNIQUE INDEX uq_operator_email_ci "
        "ON operator (lower(email));"
    )

    # ── operator_role ─────────────────────────────────────────────────────
    op.create_table(
        "operator_role",
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
        sa.Column("role", sa.Text(), nullable=False),
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
        sa.CheckConstraint(
            "role IN ('operator','connector_admin','founder_admin','auditor')",
            name="ck_operator_role_value",
        ),
    )
    op.create_index(
        "ix_operator_role_operator_id", "operator_role", ["operator_id"]
    )
    # At most one active role per operator. The partial index is the
    # DB-level guard; the application code reads the row WHERE
    # revoked_at IS NULL.
    op.execute(
        "CREATE UNIQUE INDEX uq_operator_role_active "
        "ON operator_role (operator_id) WHERE revoked_at IS NULL;"
    )

    # ── connector_authorization ───────────────────────────────────────────
    op.create_table(
        "connector_authorization",
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
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "authorized_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "authorized_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operator.id"),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operator.id"),
            nullable=True,
        ),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column(
            "poll_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("300"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','active','revoked','locked')",
            name="ck_connector_authorization_status",
        ),
    )
    op.create_index(
        "ix_connector_authorization_lea_partner",
        "connector_authorization",
        ["lea_id", "partner"],
    )
    # The dashboard's per-LEA partner column reads "current authz per
    # (lea, partner)". A unique partial index on the non-revoked rows
    # keeps the lookup honest: at most one active or pending row per
    # (lea, partner) pair.
    op.execute(
        "CREATE UNIQUE INDEX uq_connector_authorization_live "
        "ON connector_authorization (lea_id, partner) "
        "WHERE revoked_at IS NULL;"
    )

    # ── audit_log ─────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
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
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column(
            "lea_id",
            sa.String(),
            sa.ForeignKey("leas.id"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "detail", sa.dialects.postgresql.JSONB(), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_audit_log_operator_created",
        "audit_log",
        ["operator_id", "created_at"],
    )
    op.create_index(
        "ix_audit_log_lea_created", "audit_log", ["lea_id", "created_at"]
    )
    op.create_index(
        "ix_audit_log_action_created",
        "audit_log",
        ["action", "created_at"],
    )

    # ── Grants ────────────────────────────────────────────────────────────
    # edlink_app: read-only on the three auth tables. The validator
    # never writes; the admin app routes via edlink_ops for writes.
    op.execute(
        """
        GRANT SELECT ON
            operator, operator_role, connector_authorization
        TO edlink_app;
        """
    )

    # edlink_ops: SELECT + INSERT on all four. Column-level UPDATE
    # on the supersession columns so role revoke + connector revoke
    # flows work without granting full row UPDATE.
    op.execute(
        """
        GRANT SELECT, INSERT ON
            operator, operator_role,
            connector_authorization, audit_log
        TO edlink_ops;

        GRANT UPDATE (last_seen_at, status) ON operator TO edlink_ops;
        GRANT UPDATE (revoked_at, revoked_by) ON operator_role TO edlink_ops;
        GRANT UPDATE (
            status,
            authorized_at, authorized_by,
            revoked_at, revoked_by,
            secret_ref, poll_interval_seconds, notes
        ) ON connector_authorization TO edlink_ops;
        """
    )

    # edlink_dba: full. Retention + break-glass.
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON
            operator, operator_role,
            connector_authorization, audit_log
        TO edlink_dba;
        """
    )

    # ── Bootstrap rows ────────────────────────────────────────────────────
    # Env-driven: BOOTSTRAP_OPERATOR_SUBJECTS, BOOTSTRAP_OPERATOR_EMAILS,
    # BOOTSTRAP_OPERATOR_NAMES are parallel comma-separated lists. The
    # first row references itself for granted_by so the schema's
    # NOT NULL FK is satisfied at the founder pair. Test envs leave
    # these unset; the dev seed handles its own test personas.
    _bootstrap_founder_operators()


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_log_action_created;")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_lea_created;")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_operator_created;")
    op.drop_table("audit_log")

    op.execute(
        "DROP INDEX IF EXISTS uq_connector_authorization_live;"
    )
    op.execute(
        "DROP INDEX IF EXISTS ix_connector_authorization_lea_partner;"
    )
    op.drop_table("connector_authorization")

    op.execute("DROP INDEX IF EXISTS uq_operator_role_active;")
    op.execute("DROP INDEX IF EXISTS ix_operator_role_operator_id;")
    op.drop_table("operator_role")

    op.execute("DROP INDEX IF EXISTS uq_operator_email_ci;")
    op.drop_table("operator")


def _bootstrap_founder_operators() -> None:
    subjects = _split_env("BOOTSTRAP_OPERATOR_SUBJECTS")
    emails = _split_env("BOOTSTRAP_OPERATOR_EMAILS")
    names = _split_env("BOOTSTRAP_OPERATOR_NAMES")

    if not subjects:
        return
    if len(emails) != len(subjects) or (names and len(names) != len(subjects)):
        raise RuntimeError(
            "BOOTSTRAP_OPERATOR_* env vars must be parallel "
            "comma-separated lists; lengths disagree."
        )

    bind = op.get_bind()
    operator_ids: list[uuid.UUID] = []
    for i, subject in enumerate(subjects):
        email = emails[i]
        name = names[i] if names else email.split("@")[0]
        operator_id = uuid.uuid4()
        operator_ids.append(operator_id)
        bind.execute(
            sa.text(
                """
                INSERT INTO operator (id, subject, display_name, email, status)
                VALUES (:id, :subject, :name, :email, 'active')
                ON CONFLICT (subject) DO NOTHING
                """
            ),
            {
                "id": operator_id,
                "subject": subject,
                "name": name,
                "email": email,
            },
        )

    # Bootstrap row references itself so granted_by NOT NULL is
    # satisfied at the chain root. Subsequent grants chain to a real
    # operator id via the admin app.
    for operator_id in operator_ids:
        bind.execute(
            sa.text(
                """
                INSERT INTO operator_role
                    (id, operator_id, role, granted_by, reason)
                VALUES
                    (:id, :op, 'founder_admin', :op, 'bootstrap')
                ON CONFLICT DO NOTHING
                """
            ),
            {"id": uuid.uuid4(), "op": operator_id},
        )


def _split_env(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]
