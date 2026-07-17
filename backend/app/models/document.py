import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Integer,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class VersionStatus(str, enum.Enum):
    # Registered, chunks not yet committed. Retrieval never looks here: a version with
    # no chunks would otherwise be silently unfindable, and "no guidance found" would be
    # indistinguishable from "still processing".
    PENDING = "pending"

    ACTIVE = "active"
    ARCHIVED = "archived"

    # Removed from search for a reason that is NOT supersession: a licence that forbids
    # indexing (#49 — KDIGO and NICE both prohibit an "information storage and retrieval
    # system"), a retraction, an ingest later found corrupt. Distinct from ARCHIVED on
    # purpose: archived means "a newer edition replaced this" and stays reachable by explicit
    # request (#10), which is exactly wrong here — a withdrawn version must not answer at all,
    # by any path. Retrieval includes only ACTIVE (and ARCHIVED when asked), so WITHDRAWN is
    # excluded by construction rather than by a filter someone must remember.
    #
    # Reversible: the chunks and the PDF are left in place, so flipping back to ACTIVE
    # re-indexes nothing. That is what makes it the right tool for a licence that might later
    # be granted, and it is why licensing removal is a status change, not a DELETE.
    WITHDRAWN = "withdrawn"


class Document(Base):
    """A guideline as a work — e.g. "ESC Heart Failure Guidelines" — across all editions.

    issuing_org and region are not decoration: conflict detection groups retrieved
    chunks by issuing_org, and only escalates to a comparison pass when a single
    result set spans more than one organisation.
    """

    __tablename__ = "documents"
    # The natural key. Without it two rows can describe the same guideline, versions
    # scatter across both, and supersession silently marks the wrong predecessor.
    __table_args__ = (
        # Scoped to the clinic: without it, clinic-b cannot upload a protocol whose title
        # clinic-a already used, and the rejection tells them clinic-a has it.
        #
        # NULLS NOT DISTINCT because clinic_id is NULL for published guidelines, and a
        # plain UNIQUE treats NULLs as distinct — it would quietly stop constraining the
        # public corpus, which is where duplicates actually matter.
        UniqueConstraint(
            "clinic_id",
            "issuing_org",
            "title",
            name="uq_document_clinic_org_title",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(512))
    issuing_org: Mapped[str] = mapped_column(String(128), index=True)

    # NULL means a published guideline — ESC, EASD, ESMO — belonging to every clinic.
    # NOT NULL means one clinic's own uploaded protocol. This is the only column in the
    # system where "no clinic" is a real answer rather than a gap; everywhere else a
    # missing clinic is a row row-level security cannot place.
    clinic_id: Mapped[str | None] = mapped_column(String(128), index=True)
    region: Mapped[str | None] = mapped_column(String(64), index=True)
    guideline_type: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base):
    """One edition of a guideline. Superseded editions are ARCHIVED, never deleted —
    an answer given in 2024 must stay reproducible after the 2026 edition lands.
    """

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_label", name="uq_document_version"),
        # Redundant as uniqueness — id is the primary key — but a composite foreign key
        # needs a matching unique target, and this is what lets the FK below carry
        # document_id along.
        UniqueConstraint("id", "document_id", name="uq_version_id_document"),
        CheckConstraint(
            "superseded_by IS NULL OR superseded_by <> id",
            name="ck_version_not_self_superseding",
        ),
        # The successor must belong to the same guideline. A plain FK on superseded_by
        # would accept any version anywhere, and #17 would then tell a cardiologist
        # their heart-failure guideline is superseded by an oncology document.
        ForeignKeyConstraint(
            ["superseded_by", "document_id"],
            ["document_versions.id", "document_versions.document_id"],
            name="fk_superseded_by_same_document",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version_label: Mapped[str] = mapped_column(String(64))

    # Pages whose glyphs the embedded font never named, so their chunks were refused at
    # indexing (#41). Carried on the version because that is what retrieval joins to and
    # what a citation names: a clinician reading this document's guidance is entitled to
    # know it has holes, and where.
    #
    # NULL means nobody measured — a version indexed before 0013. Empty means measured and
    # clean. Those are different claims and only one of them is reassuring.
    unreadable_pages: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))  # "2021", "2023 Focused Update"
    published_at: Mapped[datetime | None] = mapped_column(Date)

    status: Mapped[VersionStatus] = mapped_column(
        # values_callable is not optional here. SQLAlchemy persists an enum's *name*
        # by default ("ACTIVE"), while migration 0001 created the type from its
        # *values* ("active"). Without this every insert fails on the type, which is
        # exactly what happened the first time anything wrote a version.
        Enum(
            VersionStatus,
            name="version_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=VersionStatus.PENDING,
        server_default="pending",
        index=True,
    )

    # sha256 of the source PDF. Proves the answer cited the same bytes we ingested —
    # but only because storage_uri keeps those bytes around to be re-checked.
    file_hash: Mapped[str] = mapped_column(String(64), unique=True)

    # Where the original PDF lives. Content-addressed, so it derives from file_hash.
    storage_uri: Mapped[str] = mapped_column(Text)

    # Set when a newer edition replaces this one. Drives the staleness warning:
    # "you are reading the 2021 guideline; a 2023 edition exists".
    superseded_by: Mapped[uuid.UUID | None] = mapped_column()

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="versions")
    chunks: Mapped[list["Chunk"]] = relationship(  # noqa: F821
        back_populates="document_version", cascade="all, delete-orphan"
    )
