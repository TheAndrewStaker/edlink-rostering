"""Dev-only endpoints. Disabled outside ``EDLINK_PROFILE=dev``.

Exposes ``POST /api/dev/mint-jwt`` so the React app's persona
switcher can sign in as any seeded operator without an IdP. The
endpoint signs an HS256 JWT against ``DEV_JWT_SECRET`` so the
production validator path stays unchanged.

The router is included in the FastAPI app at startup only when the
profile is dev; in production the routes do not exist (404 on the
path, not 401 with a hint that they could exist). Belt-and-braces:
each endpoint also re-checks the env on call so a misconfigured
prod cannot accidentally serve the route.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from edlink_rostering.core.settings import get_settings

router = APIRouter(prefix="/dev", tags=["dev"])


class MintJwtRequest(BaseModel):
    subject: str = Field(min_length=1)
    email: str | None = None
    name: str | None = None
    expires_in_minutes: int = Field(default=60, ge=1, le=720)


class MintJwtResponse(BaseModel):
    token: str
    expires_at: datetime


def is_dev_profile() -> bool:
    return get_settings().is_dev_profile()


@router.post(
    "/mint-jwt",
    response_model=MintJwtResponse,
    operation_id="dev.mint_jwt",
)
async def mint_jwt(body: MintJwtRequest) -> MintJwtResponse:
    """Sign a dev-only JWT for a seeded operator subject.

    Returns 404 outside dev so a curious probe cannot tell that the
    endpoint exists. The dev React app calls this on persona
    switch and stashes the token in localStorage under
    ``edlink.jwt``.
    """

    if not is_dev_profile():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found.",
        )
    secret = get_settings().dev_jwt_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DEV_JWT_SECRET not set.",
        )

    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=body.expires_in_minutes)
    claims: dict[str, object] = {
        "sub": body.subject,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "nbf": int(now.timestamp()),
    }
    if body.email:
        claims["email"] = body.email
    if body.name:
        claims["name"] = body.name
    token = jwt.encode(claims, secret, algorithm="HS256")
    return MintJwtResponse(token=token, expires_at=expires_at)
