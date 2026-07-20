"""Gated registration over HTTP (#51): a clinic-admin mints invites, a newcomer redeems one.

Two endpoints on opposite sides of the trust boundary, and the asymmetry is the whole design:

- POST /invites is the most privileged thing here — it opens a tenant boundary — so it needs a
  verified clinic-admin, and the invite is scoped to THAT admin's clinic, taken from the token.
  There is no clinic_id in the request body: an admin cannot invite into a clinic they are not
  in, the same way a query cannot name its own actor.
- POST /register is the ONLY unauthenticated write in the system, because a newcomer has no
  token yet. The invite code stands in for the token as the gate — high-entropy, single-use,
  expiring — and the clinic it lands them in is the invite's, never the request's. A wrong code
  is answered generically ("invalid or expired"), so the endpoint never confirms which codes
  exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Clinician, require_clinic_admin
from app.db.session import get_session
from app.services.identity import (
    IdentityProvider,
    IdentityUnavailable,
    UsernameTaken,
)
from app.services.invites import InviteError, create_invite
from app.services.registration import register_with_invite

router = APIRouter(tags=["registration"])

# Bounds on how long an invite can live. A default that is convenient and a ceiling that keeps a
# leaked code from being useful for weeks.
_DEFAULT_TTL_HOURS = 72
_MAX_TTL_HOURS = 24 * 14


def get_identity_provider() -> IdentityProvider:  # pragma: no cover - overridden at startup/tests
    raise RuntimeError(
        "no identity provider is wired: the Keycloak Admin provider is step 3, "
        "and tests override this dependency with a fake"
    )


class InviteRequest(BaseModel):
    # No clinic_id: an invite is always for the admin's own clinic, from the token. A body field
    # would be a client-settable tenant boundary — exactly what auth.py refuses elsewhere.
    role: str | None = None
    ttl_hours: int = Field(default=_DEFAULT_TTL_HOURS, ge=1, le=_MAX_TTL_HOURS)


class InviteResponse(BaseModel):
    # The raw code, returned once and never again — the admin shares it, and only its hash is
    # stored. clinic_id echoes back what the invite was scoped to, so the admin can see it.
    code: str
    clinic_id: str
    role: str | None
    expires_at: datetime


class RegisterRequest(BaseModel):
    code: str
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    name: str | None = None


class RegisterResponse(BaseModel):
    user_id: str


@router.post("/invites", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def mint_invite(
    body: InviteRequest,
    admin: Clinician = Depends(require_clinic_admin),
    session: AsyncSession = Depends(get_session),
) -> InviteResponse:
    """Mint an invite for the admin's own clinic. The raw code is in the response once."""
    expires_at = datetime.now(UTC) + timedelta(hours=body.ttl_hours)
    invite, code = await create_invite(
        session,
        clinic_id=admin.clinic_id,
        created_by=admin.actor_id,
        expires_at=expires_at,
        role=body.role,
    )
    await session.commit()
    return InviteResponse(
        code=code, clinic_id=invite.clinic_id, role=invite.role, expires_at=invite.expires_at
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
    idp: IdentityProvider = Depends(get_identity_provider),
) -> RegisterResponse:
    """Redeem an invite and create the account it grants. Unauthenticated by necessity — the
    code is the gate. Every failure rolls the transaction back, so a code is never spent unless
    the account was actually made."""
    try:
        sub = await register_with_invite(
            session,
            idp,
            code=body.code,
            username=body.username,
            password=body.password,
            name=body.name,
            now=datetime.now(UTC),
        )
        await session.commit()
        return RegisterResponse(user_id=sub)
    except InviteError:
        # Unknown, expired, or already used. One message for all three — telling them apart
        # would confirm which codes exist.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired invite code"
        ) from None
    except UsernameTaken:
        # The code is fine; the username is not. Roll back so the invite stays usable with a
        # different username.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="username is already taken"
        ) from None
    except IdentityUnavailable:
        # The provider is down. Un-spend the code — a Keycloak hiccup must not burn an invite.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="registration is temporarily unavailable; try again",
        ) from None
