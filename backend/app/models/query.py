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
    is stamped, while the row and its `text_hash` remain. Deleting the row instead
    would force an UPDATE or DELETE on audit_log to clear the reference — which the
    append-only trigger rejects. Redaction keeps both guarantees intact at once:
    the content is gone, and the chain still proves which question was asked.
    """

    __tablename__ = "queries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Opaque clinician identifier. Never a name — resolve against your IdP.
    actor_id: Mapped[str] = mapped_column(String(128), index=True)

    # NULL once redacted under a GDPR erasure request.
    text: Mapped[str | None] = mapped_column(Text)

    # sha256 of the original text. Survives redaction, so an audit row can still be
    # matched to a question presented in evidence, without us retaining the question.
    text_hash: Mapped[str] = mapped_column(String(64), index=True)

    language: Mapped[str | None] = mapped_column(String(16))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_redacted(self) -> bool:
        return self.redacted_at is not None
