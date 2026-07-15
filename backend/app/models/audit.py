import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The chain's anchor. seq=1 carries this as its prev_hash.
GENESIS_HASH = "0" * 64


class AuditLog(Base):
    """Append-only, hash-chained record of every answered query.

    Append-only is enforced in the database (a BEFORE UPDATE/DELETE/TRUNCATE trigger,
    plus REVOKE on the app role) — not in this class. An ORM-level guard would be
    advisory only; anything holding a connection could bypass it.

    Nothing here is written twice. `seq` is a gapless-by-construction ordering key,
    and each row's `row_hash` covers the previous row's, so removing or editing any
    row breaks verification from that point forward.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        # Answers the containment check in migration 0008's delete trigger. Without it
        # that check is a sequential scan of this table on every chunk delete.
        Index(
            "ix_audit_log_retrieved_chunk_ids",
            "retrieved_chunk_ids",
            postgresql_using="gin",
        ),
    )

    # BIGSERIAL, not UUID: the chain is an ordered structure and needs a total order
    # that does not depend on clock skew.
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    prev_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64), unique=True)

    actor_id: Mapped[str] = mapped_column(String(128), index=True)

    # No ON DELETE clause by design: queries are redacted, never deleted, so this
    # reference is stable for the life of the row.
    query_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("queries.id"), index=True)
    query_hash: Mapped[str] = mapped_column(String(64))

    # The exact passages the answer was built from. With chunks bound to a
    # document_version, this resolves to "which edition, which page" years later.
    #
    # No foreign key — Postgres cannot reference an element of an array. A trigger on
    # `chunks` enforces the same thing instead (migration 0008): a cited passage cannot
    # be deleted, so this list always resolves. Deleting one never broke the hash chain,
    # which is precisely why it needed its own guard: the chain would have kept verifying
    # over a trail that could no longer show what it relied on.
    retrieved_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))

    # Hash of the rendered prompt and the model identifier: reproducibility evidence
    # without storing prompt text on every row.
    prompt_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))

    # Structured extractive answer (see app.schemas.answer.AnswerPayload) — statements
    # with citations, never free-form clinical advice. Stored as given to the clinician.
    response: Mapped[dict] = mapped_column(JSONB)
    response_hash: Mapped[str] = mapped_column(String(64))

    # Set in Python, not by the server: it is part of the hashed payload, so the
    # writer and the verifier must agree on the exact value.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    error: Mapped[str | None] = mapped_column(Text)
