"""The record that an erasure happened (#28).

Its own chain, because `audit_log`'s payload schema is frozen: adding a single nullable
key to `_row_payload` changes `_canonical`'s bytes for every row already written and
fails the whole chain. That was decided the day 0001 shipped, whether or not anyone
noticed.

Same discipline all the same. An erasure record an operator can quietly remove is a
record of nothing — it would let "we erased it" and "we said we erased it" become
indistinguishable, and those are exactly the two things a regulator is asking to tell
apart.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Same sentinel the audit chain uses for its first row: a chain has to start somewhere,
# and it must start somewhere fixed.
ERASURE_GENESIS_HASH = "0" * 64


class LegalBasis(str, enum.Enum):
    """Why an erasure was performed. An enum, and that is the security control.

    A free-text `reason` is where someone writes "erased the question about the enalapril
    dose for the patient in bed 4" — re-creating, in an append-only table, the thing that
    was just destroyed. The issue calls this the recursion. The fix is not a warning in a
    code review; it is that no field exists to hold prose.
    """

    CONSENT_WITHDRAWN = "consent_withdrawn"
    NO_LONGER_NECESSARY = "no_longer_necessary"
    UNLAWFUL_PROCESSING = "unlawful_processing"
    OBJECTION_UPHELD = "objection_upheld"
    LEGAL_OBLIGATION = "legal_obligation"
    # Not a GDPR basis. An operator erasing for their own reasons is a real thing that
    # happens, and recording it honestly beats mislabelling it as a data-subject right.
    OPERATOR_REQUEST = "operator_request"


class ErasureLog(Base):
    __tablename__ = "erasure_log"
    __table_args__ = (
        Index("ix_erasure_log_clinic_seq", "clinic_id", "seq"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    prev_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64), unique=True)

    # A random UUID (`uuid4`), not derived from the question. Recording it points at a
    # row whose content is gone; it does not reconstitute it.
    query_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)

    # Taken from the query, not passed in: the erasure record belongs to the same chain
    # as the thing it erased.
    clinic_id: Mapped[str] = mapped_column(String(128))

    # From the token (#30). Never from a request body — an erasure attributed to whoever
    # the client says performed it is not a record of who performed it.
    erased_by: Mapped[str] = mapped_column(String(128))

    legal_basis: Mapped[str] = mapped_column(String(32))

    erased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
