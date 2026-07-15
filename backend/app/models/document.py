import enum
import uuid
from datetime import datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class VersionStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Document(Base):
    """A guideline as a work — e.g. "ESC Heart Failure Guidelines" — across all editions.

    issuing_org and region are not decoration: conflict detection groups retrieved
    chunks by issuing_org, and only escalates to a comparison pass when a single
    result set spans more than one organisation.
    """

    __tablename__ = "documents"
    # The natural key. Without it two rows can describe the same guideline, versions
    # scatter across both, and supersession silently marks the wrong predecessor.
    __table_args__ = (UniqueConstraint("issuing_org", "title", name="uq_document_org_title"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(512))
    issuing_org: Mapped[str] = mapped_column(String(128), index=True)
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
    __table_args__ = (UniqueConstraint("document_id", "version_label", name="uq_document_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version_label: Mapped[str] = mapped_column(String(64))  # "2021", "2023 Focused Update"
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
        default=VersionStatus.ACTIVE,
        index=True,
    )

    # sha256 of the source PDF. Proves the answer cited the same bytes we ingested —
    # but only because storage_uri keeps those bytes around to be re-checked.
    file_hash: Mapped[str] = mapped_column(String(64), unique=True)

    # Where the original PDF lives. Content-addressed, so it derives from file_hash.
    storage_uri: Mapped[str] = mapped_column(Text)

    # Set when a newer edition replaces this one. Drives the staleness warning:
    # "you are reading the 2021 guideline; a 2023 edition exists".
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="versions")
    successor: Mapped["DocumentVersion | None"] = relationship(remote_side=[id])
    chunks: Mapped[list["Chunk"]] = relationship(  # noqa: F821
        back_populates="document_version", cascade="all, delete-orphan"
    )
