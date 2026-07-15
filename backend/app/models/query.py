import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Query(Base):
    """The erasable side of the GDPR boundary.

    A clinician's question ("45yo male, reduced EF, on ACE-i...") is pseudo-personal
    data and must be erasable on request. The audit chain must survive that erasure.

    Erasure is therefore *redaction, not deletion*: `text` is nulled and `redacted_at`
    is stamped, while the row remains. Deleting the row instead would force an UPDATE or
    DELETE on audit_log to clear the reference — which the append-only trigger rejects.

    **`text_salt` is nulled too, and that is what makes the erasure real.** This docstring
    used to end "the content is gone, and the chain still proves which question was
    asked", and both halves cannot be true at once. `text_hash` was `sha256(text)`,
    unsalted; clinical questions are enumerable, and 20 of 20 "erased" questions were
    recovered from the surviving hash in 0.3 ms each. The hash *was* the content. See
    migration 0010.
    """

    __tablename__ = "queries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Opaque clinician identifier. Never a name — resolve against your IdP.
    actor_id: Mapped[str] = mapped_column(String(128), index=True)

    # Which clinic asked. From the token, never a request body — same discipline as
    # actor_id, and for a sharper reason: this is the value row-level security filters on,
    # so a client-settable clinic_id is a client-settable tenant boundary.
    clinic_id: Mapped[str] = mapped_column(String(128), index=True)

    # NULL once redacted under a GDPR erasure request.
    text: Mapped[str | None] = mapped_column(Text)

    # sha256(text_salt || text). Survives redaction as bytes, because audit_log hashes
    # it and audit_log cannot change — but once the salt is gone it is unverifiable, and
    # that is deliberate rather than unfortunate. Someone holding the question can no
    # longer confirm it was this one; neither can anyone guessing. Those were always the
    # same capability.
    text_hash: Mapped[str] = mapped_column(String(64), index=True)

    # 32 random bytes, hex. Never in audit_log — it has to live somewhere erasable, and
    # audit_log is the one table that is not.
    #
    # NULL means one of two things and `redacted_at` tells them apart: erased (salt
    # destroyed), or written before migration 0010 and never salted at all. Rows in the
    # second group are permanently un-erasable: their hash is baked into the chain.
    text_salt: Mapped[str | None] = mapped_column(String(64))

    language: Mapped[str | None] = mapped_column(String(16))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_redacted(self) -> bool:
        return self.redacted_at is not None
