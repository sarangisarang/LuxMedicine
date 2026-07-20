import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Invite(Base):
    """A single-use, expiring registration code that binds a new user to a clinic (#51).

    No one self-registers without one, and the invite — issued by an admin — is the only thing
    that sets the `clinic_id` the tenant boundary is keyed on. A registrant never chooses their
    own clinic: `core/auth.py` already refuses a client-supplied clinic_id, and this is the
    server-side counterpart — the invite says which clinic, and nothing a client sends can.

    **Only the hash of the code is stored, and unlike a question hash (#28) it needs no salt.**
    #28 learned the hard way that an unsalted sha256 of *enumerable* content (a clinical
    question) is reversible by guessing — 20 of 20 recovered in 0.3 ms each. An invite code is
    the opposite: 256 bits of `secrets` entropy, not enumerable, so sha256 of it cannot be
    reversed by brute force. Storing only the hash means a database leak exposes no usable
    code, the same reason a password store keeps hashes — the raw code is shown to the admin
    once, at creation, and never again.
    """

    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # sha256(code), hex. Unique so the same code cannot be minted twice, and the lookup key on
    # redemption — the raw code is never stored, only compared by hash.
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # The tenant this invite grants. NOT NULL and enforced non-empty at creation: an invite
    # that set no clinic_id would create a user with no tenant, whom auth.py then locks out.
    clinic_id: Mapped[str] = mapped_column(String(128), index=True)

    # Optional clinic role the invitee lands in. NULL means the realm's default.
    role: Mapped[str | None] = mapped_column(String(64))

    # The admin actor_id who issued it — issuing an invite opens a tenant boundary, so it is an
    # administrative act with an owner, recorded like any other.
    created_by: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # A leaked code is bounded by this: past it, redemption fails even if never used.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Set the moment the code is redeemed, and the gate that makes it single-use. NULL means
    # unused; once stamped, redemption refuses it.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The `sub` of the user created on redemption — who this invite became.
    consumed_by: Mapped[str | None] = mapped_column(String(128))
