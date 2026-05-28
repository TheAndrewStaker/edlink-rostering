"""Connector authorization lifecycle service.

The four verbs the connector management UI exposes (authorize, revoke,
rotate-credential, adjust-poll-interval) and the read-side list query
the partner+LEA roll-up table renders. The service layer matches the
existing ``RetryService`` / ``RevertService`` / ``QuarantineService``
pattern: each method takes a session_factory, opens its own
transaction, and writes the matching audit row in the same transaction
as the canonical change.

Audit rows go into ``audit_log`` (V0004). The non-sync audit table is
the right home for connector lifecycle events: a UNION at read time in
the future audit-log explorer will surface them alongside sync,
revert, retry, and quarantine actions per
``docs/design/admin-surfaces.md`` § "Audit log."

Authorization model:

- A ``(lea_id, partner)`` pair has at most one live row (partial unique
  index ``uq_connector_authorization_live`` enforces this at the DB
  level). Live = ``revoked_at IS NULL``.
- ``authorize()`` either flips a ``pending`` row to ``active`` or
  inserts a fresh ``active`` row when no live row exists.
- ``revoke()`` sets ``revoked_at`` and ``revoked_by`` on the live row.
  A subsequent ``authorize()`` for the same pair inserts a new row.
- ``rotate_credential()`` updates ``secret_ref`` on the live row; the
  prior name lands in the audit row's ``detail`` so the rotation
  history is queryable.
- ``adjust_poll_interval()`` updates ``poll_interval_seconds`` with a
  60s..3600s bound, audited.

Key Vault verification: ``authorize()`` and ``rotate_credential()``
verify the staged secret exists in the (mocked) Key Vault before
committing. The verify step is intentionally minimal in the POC; in
production it would also call the partner identity endpoint to verify
the token's scopes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId
from edlink_rostering.infrastructure.ports import SecretNotFound, SecretStore


ConnectorStatus = Literal["pending", "active", "revoked", "locked"]


_POLL_INTERVAL_MIN = 60
_POLL_INTERVAL_MAX = 3600


@dataclass(frozen=True)
class ConnectorAuthorizationRow:
    """One row in the integrations roll-up table.

    ``integration_status`` and ``sharing_scope`` are populated by the
    sync worker's per-drain poll against EdLink's per-integration
    endpoint (see
    :class:`edlink_rostering.services.integration_status.IntegrationStatusPoller`).
    They survive a connector revoke so historic rows still show
    the last observed partner-side state, which is how the
    Integrations page renders a meaningful "Revoked at <date>, last
    status was disabled" row in the include-revoked view.
    """

    id: uuid.UUID
    lea_id: LeaId
    lea_name: str
    partner: str
    status: ConnectorStatus
    authorized_at: datetime | None
    authorized_by_email: str | None
    revoked_at: datetime | None
    revoked_by_email: str | None
    secret_ref: str
    poll_interval_seconds: int
    notes: str | None
    integration_status: str
    sharing_scope: str | None
    integration_status_observed_at: datetime | None


@dataclass(frozen=True)
class AuthorizeOutcome:
    id: uuid.UUID
    lea_id: LeaId
    partner: str
    status: ConnectorStatus
    secret_ref: str
    poll_interval_seconds: int
    created_new_row: bool


@dataclass(frozen=True)
class RevokeOutcome:
    id: uuid.UUID
    lea_id: LeaId
    partner: str
    revoked_at: datetime


@dataclass(frozen=True)
class RotateCredentialOutcome:
    id: uuid.UUID
    lea_id: LeaId
    partner: str
    previous_secret_ref: str
    new_secret_ref: str


@dataclass(frozen=True)
class AdjustPollIntervalOutcome:
    id: uuid.UUID
    lea_id: LeaId
    partner: str
    previous_poll_interval_seconds: int
    new_poll_interval_seconds: int


class ConnectorAuthorizationError(Exception):
    """Base exception for the connector authz service."""


class ConnectorAuthorizationNotFound(ConnectorAuthorizationError):
    """Raised when an action targets a (lea, partner) without a live row."""


class ConnectorSecretNotStaged(ConnectorAuthorizationError):
    """Raised when authorize or rotate references an unknown Key Vault secret."""


class ConnectorAuthorizationConflict(ConnectorAuthorizationError):
    """Raised when an authorize attempt conflicts with an existing live row."""


class ConnectorAuthorizationService:
    """Service-layer wrapper for the connector lifecycle endpoints."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        key_vault: SecretStore,
    ) -> None:
        self._sessions = session_factory
        self._key_vault = key_vault

    # ── Reads ────────────────────────────────────────────────────────────────

    async def list_authorizations(
        self,
        authorized_leas: frozenset[LeaId] | None,
        *,
        lea_id: LeaId | None = None,
        include_revoked: bool = False,
    ) -> list[ConnectorAuthorizationRow]:
        """Return authorization rows joined with LEA + operator email.

        ``authorized_leas=None`` means "no scope filter" (used by
        owner, admin, auditor whose role implies all
        LEAs). A non-None set scopes the result, which is how the
        operator role's read-only access lands without leaking other
        LEAs' rows.

        ``lea_id`` narrows to a single LEA (used by the LEA detail
        drawer's Integration section). When set, it composes with
        ``authorized_leas`` so an operator without scope for that LEA
        still gets the empty list rather than a leak.

        ``include_revoked=True`` returns the full history including
        revoked rows, ordered with live rows first. The Integrations
        page exposes this via a toggle so revocation history is
        reachable without a separate audit query.
        """

        async with self._sessions() as session:
            select_clause = """
                SELECT
                    ca.id,
                    ca.lea_id,
                    l.name AS lea_name,
                    ca.partner,
                    ca.status,
                    ca.authorized_at,
                    auth_op.email AS authorized_by_email,
                    ca.revoked_at,
                    revoke_op.email AS revoked_by_email,
                    ca.secret_ref,
                    ca.poll_interval_seconds,
                    ca.notes,
                    ca.integration_status,
                    ca.sharing_scope,
                    ca.integration_status_observed_at
                FROM connector_authorization ca
                JOIN leas l ON l.id = ca.lea_id
                LEFT JOIN operator auth_op ON auth_op.id = ca.authorized_by
                LEFT JOIN operator revoke_op ON revoke_op.id = ca.revoked_by
                WHERE 1=1
            """
            params: dict[str, Any] = {}
            if not include_revoked:
                select_clause += " AND ca.revoked_at IS NULL"
            if authorized_leas is not None:
                select_clause += " AND ca.lea_id = ANY(:leas)"
                params["leas"] = list(authorized_leas)
            if lea_id is not None:
                select_clause += " AND ca.lea_id = :only_lea"
                params["only_lea"] = lea_id
            # Live rows first, then revoked (most-recent revoke first)
            # so the include_revoked view leads with what is current.
            select_clause += (
                " ORDER BY (ca.revoked_at IS NULL) DESC,"
                " l.name, ca.partner, ca.revoked_at DESC"
            )

            rows = (await session.execute(text(select_clause), params)).all()

        return [
            ConnectorAuthorizationRow(
                id=r.id,
                lea_id=LeaId(r.lea_id),
                lea_name=r.lea_name,
                partner=r.partner,
                status=r.status,
                authorized_at=r.authorized_at,
                authorized_by_email=r.authorized_by_email,
                revoked_at=r.revoked_at,
                revoked_by_email=r.revoked_by_email,
                secret_ref=r.secret_ref,
                poll_interval_seconds=r.poll_interval_seconds,
                notes=r.notes,
                integration_status=r.integration_status,
                sharing_scope=r.sharing_scope,
                integration_status_observed_at=r.integration_status_observed_at,
            )
            for r in rows
        ]

    # ── Writes ───────────────────────────────────────────────────────────────

    async def authorize(
        self,
        *,
        lea_id: LeaId,
        partner: str,
        secret_ref: str,
        operator_id: uuid.UUID,
        reason: str,
        poll_interval_seconds: int | None = None,
        notes: str | None = None,
    ) -> AuthorizeOutcome:
        """Activate a connector for an LEA + partner.

        Three paths:
        1. No live row exists → INSERT a fresh ``active`` row.
        2. Live row exists with ``status='pending'`` → UPDATE to ``active``.
        3. Live row exists with ``status='active'`` → idempotent no-op
           returning the existing row (still writes an audit row so the
           re-authorize attempt is on the record).
        """

        self._verify_secret(secret_ref)
        now = datetime.now(UTC)

        async with self._sessions() as session:
            await self._assert_lea_exists(session, lea_id)
            current = await self._load_live_row(session, lea_id, partner)

            outcome: AuthorizeOutcome
            previous_status: str | None = None
            if current is None:
                new_id = uuid.uuid4()
                interval = (
                    self._validate_interval(poll_interval_seconds)
                    if poll_interval_seconds is not None
                    else 300
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO connector_authorization (
                            id, lea_id, partner, status, authorized_at,
                            authorized_by, secret_ref,
                            poll_interval_seconds, notes
                        ) VALUES (
                            :id, :lea, :partner, 'active', :now,
                            :by, :secret, :interval, :notes
                        )
                        """
                    ),
                    {
                        "id": new_id,
                        "lea": lea_id,
                        "partner": partner,
                        "now": now,
                        "by": operator_id,
                        "secret": secret_ref,
                        "interval": interval,
                        "notes": notes,
                    },
                )
                outcome = AuthorizeOutcome(
                    id=new_id,
                    lea_id=lea_id,
                    partner=partner,
                    status="active",
                    secret_ref=secret_ref,
                    poll_interval_seconds=interval,
                    created_new_row=True,
                )
            elif current["status"] == "pending":
                interval = (
                    self._validate_interval(poll_interval_seconds)
                    if poll_interval_seconds is not None
                    else current["poll_interval_seconds"]
                )
                previous_status = "pending"
                await session.execute(
                    text(
                        """
                        UPDATE connector_authorization
                        SET status = 'active',
                            authorized_at = :now,
                            authorized_by = :by,
                            secret_ref = :secret,
                            poll_interval_seconds = :interval,
                            notes = COALESCE(:notes, notes)
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": current["id"],
                        "now": now,
                        "by": operator_id,
                        "secret": secret_ref,
                        "interval": interval,
                        "notes": notes,
                    },
                )
                outcome = AuthorizeOutcome(
                    id=current["id"],
                    lea_id=lea_id,
                    partner=partner,
                    status="active",
                    secret_ref=secret_ref,
                    poll_interval_seconds=interval,
                    created_new_row=False,
                )
            else:
                previous_status = current["status"]
                outcome = AuthorizeOutcome(
                    id=current["id"],
                    lea_id=lea_id,
                    partner=partner,
                    status=current["status"],
                    secret_ref=current["secret_ref"],
                    poll_interval_seconds=current["poll_interval_seconds"],
                    created_new_row=False,
                )

            await _write_audit_log(
                session=session,
                operator_id=operator_id,
                action="connector.authorized",
                target_kind="connector_authorization",
                target_id=str(outcome.id),
                lea_id=lea_id,
                reason=reason,
                detail={
                    "partner": partner,
                    "secret_ref": secret_ref,
                    "previous_status": previous_status,
                    "created_new_row": outcome.created_new_row,
                },
                created_at=now,
            )
            await session.commit()
        return outcome

    async def revoke(
        self,
        *,
        lea_id: LeaId,
        partner: str,
        operator_id: uuid.UUID,
        reason: str,
    ) -> RevokeOutcome:
        """Revoke the live authorization row for (lea, partner)."""

        now = datetime.now(UTC)
        async with self._sessions() as session:
            current = await self._load_live_row(session, lea_id, partner)
            if current is None:
                raise ConnectorAuthorizationNotFound(
                    f"No live connector_authorization for {lea_id!r} on"
                    f" {partner!r}."
                )
            await session.execute(
                text(
                    """
                    UPDATE connector_authorization
                    SET status = 'revoked',
                        revoked_at = :now,
                        revoked_by = :by
                    WHERE id = :id
                    """
                ),
                {"id": current["id"], "now": now, "by": operator_id},
            )
            await _write_audit_log(
                session=session,
                operator_id=operator_id,
                action="connector.revoked",
                target_kind="connector_authorization",
                target_id=str(current["id"]),
                lea_id=lea_id,
                reason=reason,
                detail={
                    "partner": partner,
                    "secret_ref": current["secret_ref"],
                    "previous_status": current["status"],
                },
                created_at=now,
            )
            await session.commit()
        return RevokeOutcome(
            id=current["id"],
            lea_id=lea_id,
            partner=partner,
            revoked_at=now,
        )

    async def rotate_credential(
        self,
        *,
        lea_id: LeaId,
        partner: str,
        new_secret_ref: str,
        operator_id: uuid.UUID,
        reason: str,
    ) -> RotateCredentialOutcome:
        """Swap the Key Vault secret reference on the live row."""

        self._verify_secret(new_secret_ref)
        now = datetime.now(UTC)
        async with self._sessions() as session:
            current = await self._load_live_row(session, lea_id, partner)
            if current is None:
                raise ConnectorAuthorizationNotFound(
                    f"No live connector_authorization for {lea_id!r} on"
                    f" {partner!r}."
                )
            previous_secret_ref = current["secret_ref"]
            await session.execute(
                text(
                    """
                    UPDATE connector_authorization
                    SET secret_ref = :secret
                    WHERE id = :id
                    """
                ),
                {"id": current["id"], "secret": new_secret_ref},
            )
            await _write_audit_log(
                session=session,
                operator_id=operator_id,
                action="connector.credential_rotated",
                target_kind="connector_authorization",
                target_id=str(current["id"]),
                lea_id=lea_id,
                reason=reason,
                detail={
                    "partner": partner,
                    "previous_secret_ref": previous_secret_ref,
                    "new_secret_ref": new_secret_ref,
                },
                created_at=now,
            )
            await session.commit()
        return RotateCredentialOutcome(
            id=current["id"],
            lea_id=lea_id,
            partner=partner,
            previous_secret_ref=previous_secret_ref,
            new_secret_ref=new_secret_ref,
        )

    async def adjust_poll_interval(
        self,
        *,
        lea_id: LeaId,
        partner: str,
        new_poll_interval_seconds: int,
        operator_id: uuid.UUID,
        reason: str,
    ) -> AdjustPollIntervalOutcome:
        """Change the poll interval on the live row."""

        new_interval = self._validate_interval(new_poll_interval_seconds)
        now = datetime.now(UTC)
        async with self._sessions() as session:
            current = await self._load_live_row(session, lea_id, partner)
            if current is None:
                raise ConnectorAuthorizationNotFound(
                    f"No live connector_authorization for {lea_id!r} on"
                    f" {partner!r}."
                )
            previous = current["poll_interval_seconds"]
            await session.execute(
                text(
                    """
                    UPDATE connector_authorization
                    SET poll_interval_seconds = :interval
                    WHERE id = :id
                    """
                ),
                {"id": current["id"], "interval": new_interval},
            )
            await _write_audit_log(
                session=session,
                operator_id=operator_id,
                action="connector.poll_interval_adjusted",
                target_kind="connector_authorization",
                target_id=str(current["id"]),
                lea_id=lea_id,
                reason=reason,
                detail={
                    "partner": partner,
                    "previous_poll_interval_seconds": previous,
                    "new_poll_interval_seconds": new_interval,
                },
                created_at=now,
            )
            await session.commit()
        return AdjustPollIntervalOutcome(
            id=current["id"],
            lea_id=lea_id,
            partner=partner,
            previous_poll_interval_seconds=previous,
            new_poll_interval_seconds=new_interval,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _verify_secret(self, secret_ref: str) -> None:
        try:
            self._key_vault.get_secret(secret_ref)
        except SecretNotFound as exc:
            raise ConnectorSecretNotStaged(
                f"Key Vault secret {secret_ref!r} is not staged."
            ) from exc

    def _validate_interval(self, seconds: int) -> int:
        if seconds < _POLL_INTERVAL_MIN or seconds > _POLL_INTERVAL_MAX:
            raise ValueError(
                f"poll_interval_seconds must be between {_POLL_INTERVAL_MIN}"
                f" and {_POLL_INTERVAL_MAX}."
            )
        return seconds

    async def _assert_lea_exists(
        self, session: AsyncSession, lea_id: LeaId
    ) -> None:
        row = (
            await session.execute(
                text(
                    "SELECT 1 FROM leas WHERE id = :id AND deleted_at IS NULL"
                ),
                {"id": lea_id},
            )
        ).first()
        if row is None:
            raise ConnectorAuthorizationNotFound(
                f"LEA {lea_id!r} not found or deleted."
            )

    async def _load_live_row(
        self, session: AsyncSession, lea_id: LeaId, partner: str
    ) -> dict[str, Any] | None:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, status, secret_ref, poll_interval_seconds
                    FROM connector_authorization
                    WHERE lea_id = :lea AND partner = :partner
                      AND revoked_at IS NULL
                    """
                ),
                {"lea": lea_id, "partner": partner},
            )
        ).first()
        if row is None:
            return None
        return {
            "id": row.id,
            "status": row.status,
            "secret_ref": row.secret_ref,
            "poll_interval_seconds": row.poll_interval_seconds,
        }


async def _write_audit_log(
    *,
    session: AsyncSession,
    operator_id: uuid.UUID,
    action: str,
    target_kind: str,
    target_id: str,
    lea_id: LeaId | None,
    reason: str,
    detail: dict[str, Any],
    created_at: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO audit_log (
                operator_id, action, target_kind, target_id,
                lea_id, reason, detail, created_at
            ) VALUES (
                :op, :action, :kind, :tid,
                :lea, :reason, CAST(:detail AS JSONB), :now
            )
            """
        ),
        {
            "op": operator_id,
            "action": action,
            "kind": target_kind,
            "tid": target_id,
            "lea": lea_id,
            "reason": reason,
            "detail": json.dumps(detail),
            "now": created_at,
        },
    )


__all__ = [
    "AdjustPollIntervalOutcome",
    "AuthorizeOutcome",
    "ConnectorAuthorizationConflict",
    "ConnectorAuthorizationError",
    "ConnectorAuthorizationNotFound",
    "ConnectorAuthorizationRow",
    "ConnectorAuthorizationService",
    "ConnectorSecretNotStaged",
    "ConnectorStatus",
    "RevokeOutcome",
    "RotateCredentialOutcome",
]
