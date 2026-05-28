"""Operator authentication for the admin API.

Replaces the Phase 1 mock ``X-Operator-Identity`` header with a real
JWT validator. The seam is what matters; the IdP behind it is a config
swap that lands once the IdP (Azure Entra vs Auth0 vs in-app) is selected.

The module exports three FastAPI dependencies plus an ``Operator``
dataclass:

* ``current_operator`` validates the JWT, upserts the ``operator`` row,
  loads the active role, computes the authorized LEA set, and returns
  an ``Operator``. Every authenticated route depends on this.

* ``require(min_role)`` is a factory returning a dependency that
  enforces a minimum role. Read endpoints use ``require("auditor")``
  so the auditor role passes; action endpoints use
  ``require("operator")`` which excludes auditor.

* ``audit_operator`` is a semantic alias for ``current_operator`` used
  by handlers that write an audit row, so the dependency line makes
  the intent obvious at the call site.

Dev mode signs with HS256 against ``DEV_JWT_SECRET``. The matching
test helper at ``tests/fixtures/auth.py`` mints JWTs against the same
secret. Production swaps the validator for a JWKS-backed RS256 lookup
once the IdP is selected; the seam keeps the routes unchanged.

Authorized LEA set:

* ``owner`` and ``admin``: implicit access to every non-deleted LEA.
  The set is loaded directly from ``leas``.
* ``auditor``: read-only, same scope (all LEAs).
* ``operator``: explicit access only via ``operator_lea_grant``
  (V0005). The multi-tenancy enforcement on action endpoints reads
  from it; an operator with no grants sees an empty result set on
  read endpoints rather than a 403 leak.

Role rename history: V0011 renamed ``founder_admin`` to ``owner``
and ``connector_admin`` to ``admin``. The literal type below tracks
the post-rename values; existing rows were migrated by V0011's
``UPDATE`` pass so historical audit-log entries already reference
the new names by the time this module loads.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.dependencies import get_jwt_validator, get_session_factory
from edlink_rostering.core.types import LeaId
from edlink_rostering.infrastructure.ports import JWTValidationError, JWTValidator

# OpenAPI security scheme. ``auto_error=False`` so missing or non-bearer
# credentials surface as ``None`` here and we raise our own 401 with a
# stable message (HTTPBearer's default raises 403, which is wrong per
# RFC 7235 for missing creds). The dep still emits the
# ``HTTPBearer`` security scheme into the OpenAPI document so generated
# clients know to attach the Authorization header.
_bearer_scheme = HTTPBearer(
    bearerFormat="JWT",
    auto_error=False,
    description=(
        "Operator JWT. Dev profile mints these via"
        " ``POST /api/dev/mint-jwt``; production swaps the validator"
        " for a JWKS lookup once the IdP is selected."
    ),
)

OperatorStatus = Literal["active", "disabled", "locked"]
Role = Literal["operator", "admin", "owner", "auditor"]


@dataclass(frozen=True)
class Operator:
    """The authenticated operator backing an authenticated request."""

    id: uuid.UUID
    subject: str
    email: str
    display_name: str
    role: Role
    status: OperatorStatus
    authorized_leas: frozenset[LeaId]


# Role partial order. owner >= admin >= operator. auditor is parallel
# to operator on read endpoints only; require() encodes that
# asymmetry per minimum-role keyword.
_ROLES_SATISFYING: dict[str, frozenset[str]] = {
    "auditor": frozenset({"auditor", "operator", "admin", "owner"}),
    "operator": frozenset({"operator", "admin", "owner"}),
    "admin": frozenset({"admin", "owner"}),
    "owner": frozenset({"owner"}),
}


class DevJWTValidator:
    """HS256 validator for the dev profile.

    Satisfies :class:`~edlink_rostering.infrastructure.ports.JWTValidator`.
    Production swaps this for a JWKS-backed RS256 implementation; the
    factory in ``dependencies.get_jwt_validator()`` selects which class
    to instantiate based on the runtime profile.
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def decode(self, token: str) -> dict[str, object]:
        if not self._secret:
            raise JWTValidationError(
                "DEV_JWT_SECRET is not set. Set it in .env or use the"
                " production JWKS path."
            )
        try:
            return jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                options={"require": ["exp", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise JWTValidationError(
                "Token expired.", expired=True
            ) from None
        except jwt.InvalidTokenError as exc:
            raise JWTValidationError(f"Invalid token: {exc}") from None


async def _load_or_create_operator(
    session: AsyncSession,
    subject: str,
    email: str,
    display_name: str,
) -> tuple[uuid.UUID, OperatorStatus]:
    """Return (operator_id, status) for the JWT subject.

    A first-time subject is upserted as ``status='active'`` with no
    role grant. The caller raises 403 when the role lookup returns no
    row, so a freshly-created operator authenticates but cannot act.
    """

    row = (
        await session.execute(
            text("SELECT id, status FROM operator WHERE subject = :s"),
            {"s": subject},
        )
    ).first()
    if row is not None:
        return row.id, row.status

    new_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO operator
                (id, subject, display_name, email, status)
            VALUES
                (:id, :sub, :name, :email, 'active')
            """
        ),
        {
            "id": new_id,
            "sub": subject,
            "name": display_name,
            "email": email,
        },
    )
    return new_id, "active"


async def _load_active_role(
    session: AsyncSession, operator_id: uuid.UUID
) -> Role | None:
    row = (
        await session.execute(
            text(
                """
                SELECT role FROM operator_role
                WHERE operator_id = :id AND revoked_at IS NULL
                """
            ),
            {"id": operator_id},
        )
    ).first()
    if row is None:
        return None
    return row.role  # type: ignore[no-any-return]


async def _load_authorized_leas(
    session: AsyncSession, role: Role, operator_id: uuid.UUID
) -> frozenset[LeaId]:
    """Compute the set of LEAs this operator can act on.

    owner, admin, and auditor see every active LEA. The 'operator'
    role gets explicit grants from ``operator_lea_grant`` (V0005);
    the join filters to LEAs that are not soft-deleted so a revoked
    LEA does not leak into the set.
    """

    if role in ("owner", "admin", "auditor"):
        rows = (
            await session.execute(
                text("SELECT id FROM leas WHERE deleted_at IS NULL")
            )
        ).all()
        return frozenset(LeaId(r.id) for r in rows)

    rows = (
        await session.execute(
            text(
                """
                SELECT g.lea_id
                FROM operator_lea_grant g
                JOIN leas l ON l.id = g.lea_id
                WHERE g.operator_id = :op
                  AND g.revoked_at IS NULL
                  AND l.deleted_at IS NULL
                """
            ),
            {"op": operator_id},
        )
    ).all()
    return frozenset(LeaId(r.lea_id) for r in rows)


async def current_operator(
    creds: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ] = None,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    validator: JWTValidator = Depends(get_jwt_validator),
) -> Operator:
    """Validate the JWT and return the authenticated operator.

    On success: upserts the operator row, refreshes ``last_seen_at``,
    loads the active role and the authorized LEA set, returns the
    ``Operator`` dataclass. On failure: 401 for auth-layer issues,
    403 only at ``require()`` for role-layer issues.
    """

    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required.",
        )
    try:
        claims = validator.decode(creds.credentials)
    except JWTValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.detail,
        ) from None

    subject = str(claims.get("sub") or "")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing sub claim.",
        )
    email = str(claims.get("email") or "")
    name = str(claims.get("name") or email.split("@")[0] or subject)

    async with factory() as session:
        op_id, op_status = await _load_or_create_operator(
            session, subject, email, name
        )
        if op_status != "active":
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Operator {op_status}.",
            )

        await session.execute(
            text(
                "UPDATE operator SET last_seen_at = now() WHERE id = :id"
            ),
            {"id": op_id},
        )

        role = await _load_active_role(session, op_id)
        # We commit the upsert + last_seen_at regardless of the role
        # outcome so a first-time subject's row persists even when the
        # caller 403s at require().
        if role is None:
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Operator has no active role. Contact an owner"
                    " to grant access."
                ),
            )

        authorized = await _load_authorized_leas(session, role, op_id)
        await session.commit()

    return Operator(
        id=op_id,
        subject=subject,
        email=email,
        display_name=name,
        role=role,
        status="active",
        authorized_leas=authorized,
    )


def require(min_role: str) -> Callable[..., object]:
    """Build a FastAPI dependency that enforces a minimum role.

    Read endpoints use ``require("auditor")`` so an auditor passes.
    Action endpoints use ``require("operator")`` which excludes
    auditor. Integration lifecycle endpoints use
    ``require("admin")``. Owner-only endpoints use ``require("owner")``.
    """

    if min_role not in _ROLES_SATISFYING:
        raise ValueError(
            f"Unknown min_role {min_role!r}. Expected one of"
            f" {sorted(_ROLES_SATISFYING)}."
        )
    allowed = _ROLES_SATISFYING[min_role]

    async def dep(
        op: Operator = Depends(current_operator),
    ) -> Operator:
        if op.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role {op.role!r} cannot access an endpoint that"
                    f" requires {min_role!r}."
                ),
            )
        return op

    return dep


def require_lea_scope_at(min_role: str = "auditor") -> Callable[..., object]:
    """Build a dependency that enforces a role gate AND per-LEA scope.

    Use on per-LEA drill-down routes shaped as
    ``/api/leas/{lea_id}/...``. Pulls ``lea_id`` from the path
    (FastAPI matches the dep's parameter name against the path
    placeholder), runs the role check first, then verifies the
    operator role's explicit-grant set contains the target.

    ``owner``, ``admin``, and ``auditor`` are implicitly scoped to
    every non-deleted LEA per V0005; the scope check is skipped for
    those roles so the existing "unknown-LEA-returns-empty-list"
    info-disclosure-free semantic on drill-down reads is preserved.
    The check binds for the ``operator`` role, which gets explicit
    grants from ``operator_lea_grant`` and would otherwise be able
    to hit any drill-down URL by guessing the LEA id.
    """

    base = require(min_role)

    async def dep(
        lea_id: str,
        op: Operator = Depends(base),
    ) -> Operator:
        if op.role == "operator" and lea_id not in op.authorized_leas:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Operator is not authorized for LEA {lea_id!r}."
                ),
            )
        return op

    return dep


# Semantic alias. Endpoints that write audit rows use this to make the
# intent obvious at the dependency line.
audit_operator = current_operator


__all__ = [
    "DevJWTValidator",
    "Operator",
    "OperatorStatus",
    "Role",
    "audit_operator",
    "current_operator",
    "require",
    "require_lea_scope_at",
]
