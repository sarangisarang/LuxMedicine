import uuid  # noqa: F401  — kept for symmetry with the other model modules
from datetime import datetime

from sqlalchemy import Index, BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChainCheckpoint(Base):
    """A record that the chain was intact through a given seq at a given time.

    Turns "the chain is broken" into "the chain broke after 2026-07-15T12:00Z" — the
    difference between knowing something happened and knowing when, which is the
    difference between an incident and an investigation.

    Worth exactly what this database's integrity is worth: anyone who can rewrite
    audit_log can rewrite this. The append-only trigger (0009) means they must defeat two
    guards, not one, and that is all it means. The bound becomes real when a checkpoint is
    copied off this machine — see `export_checkpoint()`.
    """

    __tablename__ = "chain_checkpoints"
    __table_args__ = (
        Index("ix_chain_checkpoints_clinic", "clinic_id", "verified_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # No foreign key to audit_log.seq: this describes what was observed, and must survive
    # to describe a chain that has since been damaged.
    verified_through_seq: Mapped[int] = mapped_column(BigInteger)
    tail_row_hash: Mapped[str] = mapped_column(String(64))
    entries_verified: Mapped[int] = mapped_column(BigInteger)

    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    verified_by: Mapped[str] = mapped_column(String(128))

    # Checkpoints are per clinic because chains are (0011). One checkpoint over "the
    # chain" would be a claim about a chain that no longer exists.
    clinic_id: Mapped[str] = mapped_column(String(128))
