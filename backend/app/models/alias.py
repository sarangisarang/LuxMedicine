import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DrugAlias(Base):
    """One brand name and the generic it means.

    The gap this fills was measured, not guessed: multilingual-e5-large scores a
    "Renitec dose" query nearer a metformin passage than an enalapril one — below chance
    — because nothing in its training tells it Renitec *is* enalapril. Embeddings cannot
    reason their way to a fact they were never shown.
    """

    __tablename__ = "drug_aliases"
    __table_args__ = (
        CheckConstraint("alias = lower(alias)", name="ck_alias_lowercase"),
        CheckConstraint("alias <> lower(generic_name)", name="ck_alias_not_self"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Unique: one brand cannot mean two generics. Without it a duplicate row would make
    # the wrong drug reachable by name, permanently and quietly.
    alias: Mapped[str] = mapped_column(String(128), unique=True)

    generic_name: Mapped[str] = mapped_column(String(128), index=True)

    # Where this mapping came from — a registry, a package insert, a person. A wrong
    # alias answers confidently about the wrong drug, so it must be traceable to whoever
    # asserted it.
    source: Mapped[str] = mapped_column(String(256))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
