"""Register a new user by redeeming an invite (#51).

The orchestration that ties the invite lifecycle to the identity provider, and the one place
the ordering matters:

    redeem (atomic, consumes the code)  ->  create the user  ->  record the subject

Consume BEFORE create, so a code raced by two registrations is spent exactly once — the create
never runs twice. And do both in the caller's single transaction, so a create that fails rolls
the consume back with it: the invite returns to unused and can be retried, rather than a code
burned on a Keycloak hiccup. The caller (the endpoint, step 2b) owns commit/rollback; this
function only re-raises, because a half-finished registration must never look like a success.

The clinic_id handed to the provider is the INVITE's, never anything the registrant sent — that
is the whole point of gating registration behind an invite, and the server-side half of auth.py's
rule that a client can't choose its own tenant.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invite import Invite
from app.services.identity import IdentityProvider
from app.services.invites import redeem_invite


async def register_with_invite(
    session: AsyncSession,
    idp: IdentityProvider,
    *,
    code: str,
    username: str,
    password: str,
    email: str,
    now: datetime,
    name: str | None = None,
) -> str:
    """Redeem the invite and create the user it grants. Returns the new subject id.

    Raises an invites.InviteError (unknown / expired / already-used code) before touching the
    provider, or an identity.IdentityError if user creation fails. On either, the caller rolls
    back and the invite is left as it was.
    """
    # Atomic gate first: this both proves the code is valid and spends it, so no two callers
    # get past here on the same code. consumed_by is the username that claimed it; the subject
    # id is not known until the account exists, and is recorded below.
    invite = await redeem_invite(session, code=code, consumed_by=username, now=now)

    # The clinic is the invite's, full stop. A registrant cannot influence it.
    sub = await idp.create_user(
        username=username,
        password=password,
        email=email,
        clinic_id=invite.clinic_id,
        name=name,
        role=invite.role,
    )

    # The account exists; record which subject this invite became. Same transaction as the
    # consume, so the two commit together or roll back together.
    await session.execute(
        update(Invite).where(Invite.id == invite.id).values(consumed_by=sub)
    )
    return sub
