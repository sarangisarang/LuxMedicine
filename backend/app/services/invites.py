"""The invite lifecycle: mint a code, redeem it once, bind it to a clinic (#51).

Pure application logic over the `invites` table — no Keycloak here. Creating the user in the
identity provider is a later step; this owns the part that must be exactly right and is fully
testable on its own: a code is single-use, expiring, high-entropy, and carries the clinic_id a
registrant may never choose for themselves.

**Redemption is one atomic UPDATE, not read-then-write.** A code used twice at once — two people
racing the same leaked link — must succeed exactly once. `SELECT, check, UPDATE` has a window
where both reads see it unused; a single `UPDATE ... WHERE consumed_at IS NULL AND not expired`
does not. The row is the lock. Only on the failure path does a follow-up read say *why* it
failed, and a race there is harmless — it only labels an already-failed redemption.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invite import Invite

# 32 bytes = 256 bits of entropy. token_urlsafe gives ~43 URL-safe characters — not guessable,
# and the reason the stored sha256 needs no salt (see Invite's docstring).
_CODE_BYTES = 32


class InviteError(Exception):
    """A redemption that did not succeed. Subclasses say why — but see redeem_invite on why a
    public caller should be told less than a test is."""


class InviteNotFound(InviteError):
    pass


class InviteExpired(InviteError):
    pass


class InviteAlreadyConsumed(InviteError):
    pass


def generate_code() -> str:
    """A fresh, high-entropy invite code. Shown to the admin once; never stored raw."""
    return secrets.token_urlsafe(_CODE_BYTES)


def hash_code(code: str) -> str:
    """sha256(code), hex — what the row stores and what redemption compares against."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


async def create_invite(
    session: AsyncSession,
    *,
    clinic_id: str,
    created_by: str,
    expires_at: datetime,
    role: str | None = None,
) -> tuple[Invite, str]:
    """Mint an invite for a clinic. Returns the row and the RAW code — the only time the raw
    code exists; the caller shows it to the admin once and lets it go.

    clinic_id must be non-empty: an invite that granted no tenant would create a user auth.py
    then locks out, which is a support ticket, not a safety net.
    """
    if not clinic_id.strip():
        raise ValueError("an invite must name a non-empty clinic_id — it is the tenant it grants")

    code = generate_code()
    invite = Invite(
        code_hash=hash_code(code),
        clinic_id=clinic_id,
        role=role,
        created_by=created_by,
        expires_at=expires_at,
    )
    session.add(invite)
    await session.flush()
    return invite, code


async def redeem_invite(
    session: AsyncSession,
    *,
    code: str,
    consumed_by: str,
    now: datetime,
) -> Invite:
    """Consume a code, atomically and once, and return the invite whose clinic_id the new user
    must be given. Raises an InviteError subclass if the code is unknown, expired, or already
    used — the caller decides how much of that to reveal (a public /register should say only
    "invalid or expired", not "already used", which would confirm a code existed)."""
    code_hash = hash_code(code)

    consumed = (
        await session.execute(
            update(Invite)
            .where(
                Invite.code_hash == code_hash,
                Invite.consumed_at.is_(None),
                Invite.expires_at > now,
            )
            .values(consumed_at=now, consumed_by=consumed_by)
            .returning(Invite)
        )
    ).scalar_one_or_none()

    if consumed is not None:
        return consumed

    # The UPDATE matched nothing. Read the row (if any) to say why — a diagnostic, not a gate.
    existing = (
        await session.execute(select(Invite).where(Invite.code_hash == code_hash))
    ).scalar_one_or_none()
    if existing is None:
        raise InviteNotFound("no invite matches this code")
    if existing.consumed_at is not None:
        raise InviteAlreadyConsumed("this invite has already been used")
    raise InviteExpired("this invite has expired")
