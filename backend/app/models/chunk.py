import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base
from app.models.document import DocumentVersion

EMBEDDING_DIM = get_settings().embedding_dim


class Chunk(Base):
    """A retrievable passage, owned by a *version* rather than a document.

    This is the join that makes both PoC features possible: an audit row records
    chunk ids, and every chunk resolves to an exact edition and page.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal", name="uq_chunk_ordinal"),
        CheckConstraint("page_end >= page_start", name="ck_chunk_page_span"),
        # Declared here as well as in migration 0001 so that `alembic check` compares
        # like with like. Without it autogenerate sees an index the models never
        # mention and proposes dropping it — which would make the drift detector cry
        # wolf on every run, and a drift detector nobody trusts gets switched off.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )

    ordinal: Mapped[int] = mapped_column(Integer)  # position within the version

    # A chunk may span a page break, because a page break is typography rather than
    # meaning. Both are 1-based PDF page indices — see app/services/extraction.py on
    # why that is not necessarily the number printed on the page.
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)

    section: Mapped[str | None] = mapped_column(Text)

    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document_version: Mapped[DocumentVersion] = relationship(back_populates="chunks")
