"""Vector search over the corpus (#14).

Only `active` versions are searched. A pending version has no chunks and an archived one
has been replaced, so neither should answer a question — and #10's status field is what
makes "not yet indexed" and "no longer current" different from "nothing to say".

Archived versions stay reachable by explicit request. That is not a convenience: it is
how an answer given in 2024 gets re-checked in 2026 against the edition it actually
cited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.embedding import Embedder


@dataclass(frozen=True)
class SearchHit:
    chunk_id: uuid.UUID
    document_version_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    issuing_org: str
    version_label: str
    section: str | None
    page_start: int
    page_end: int
    content: str

    # Cosine distance: 0 is identical, 2 is opposite. Distance rather than a similarity
    # score because that is what the index returns — converting invites a normalisation
    # nobody can audit later.
    distance: float

    # Set when the version has a successor. #17 turns this into "a newer edition exists";
    # it is surfaced here so retrieval never hands back stale guidance silently.
    is_superseded: bool


def _base_query(embedding: list[float], *, include_archived: bool) -> Select:
    statuses = (
        [VersionStatus.ACTIVE, VersionStatus.ARCHIVED] if include_archived else [VersionStatus.ACTIVE]
    )

    return (
        select(
            Chunk.id,
            Chunk.document_version_id,
            Document.id.label("document_id"),
            Document.title,
            Document.issuing_org,
            DocumentVersion.version_label,
            Chunk.section,
            Chunk.page_start,
            Chunk.page_end,
            Chunk.content,
            Chunk.embedding.cosine_distance(embedding).label("distance"),
            DocumentVersion.superseded_by.isnot(None).label("is_superseded"),
        )
        .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        # PENDING is never included, in either mode: those chunks do not exist.
        .where(DocumentVersion.status.in_(statuses))
        .order_by(Chunk.embedding.cosine_distance(embedding))
    )


async def search(
    session: AsyncSession,
    query_text: str,
    embedder: Embedder,
    *,
    limit: int = 10,
    include_archived: bool = False,
) -> list[SearchHit]:
    """Embed the query and return the nearest chunks from searchable versions.

    The `query: ` prefix is applied by embed_query — see embedding.py on why the
    interface refuses to let a caller choose.
    """
    embedding = embedder.embed_query(query_text)

    # No hnsw.iterative_scan here, deliberately.
    #
    # pgvector documents a real trap: HNSW collects ef_search candidates by distance and
    # the status filter is applied afterwards, so a corpus dominated by archived chunks
    # can return fewer hits than asked for — quietly, as a short list rather than an
    # error. iterative_scan (0.8+) fixes it, and adding it was the obvious move.
    #
    # It could not be reproduced. At 6k chunks with 40 active, the planner filters on
    # ix_document_versions_status first and sorts those 40 exactly — the HNSW index is
    # not used at all, even with enable_seqscan off. Nothing to fix, so nothing is set:
    # a per-query SET LOCAL costs a round trip on every search to guard against a plan
    # we do not get.
    #
    # It stays a real risk at scale, when the table is large enough for HNSW *and*
    # archived chunks dominate — plausible after years of supersession. Tracked rather
    # than pre-solved; the shortfall test below is what will notice.
    rows = (await session.execute(_base_query(embedding, include_archived=include_archived).limit(limit))).all()

    return [
        SearchHit(
            chunk_id=row.id,
            document_version_id=row.document_version_id,
            document_id=row.document_id,
            document_title=row.title,
            issuing_org=row.issuing_org,
            version_label=row.version_label,
            section=row.section,
            page_start=row.page_start,
            page_end=row.page_end,
            content=row.content,
            distance=float(row.distance),
            is_superseded=bool(row.is_superseded),
        )
        for row in rows
    ]
