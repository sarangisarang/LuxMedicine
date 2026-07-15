"""Search over the corpus (#14 vector, #15 hybrid).

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

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import TEXT_SEARCH_CONFIG, Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.embedding import Embedder
from app.services.staleness import latest_labels
from app.services.synonyms import expand_query, load_aliases


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

    # Set when the version has a successor.
    is_superseded: bool

    # Pages of this version whose text could not be read, so their chunks were refused
    # (#41). Travels with the hit for the same reason is_superseded does: by the time a
    # clinician reads the quote, the fact that the document has holes is not recoverable
    # from anywhere else, and "the guideline does not say" would be indistinguishable from
    # "we could not read the page where it says it".
    #
    # None means the version predates the measurement — not that it is clean.
    unreadable_pages: list[int] | None = None

    # The label of the edition at the end of this version's supersession chain — what
    # the clinician should actually be reading. Not the immediate successor: given
    # 2021 -> 2022 -> 2023, naming 2022 sends them to read another outdated document.
    # None when nothing supersedes this version.
    superseding_version_label: str | None = None

    # Which half of the hybrid found it. Not decoration: a hit only the lexical side
    # found is usually an exact drug name or dose the embedding ranked flat, and that is
    # precisely the case #15 exists for. Worth being able to see.
    found_by_vector: bool = True
    found_by_lexical: bool = False
    rrf_score: float | None = None


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
            DocumentVersion.unreadable_pages,
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

    labels = await latest_labels(session, [row.document_version_id for row in rows])

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
            superseding_version_label=(
                labels.get(row.document_version_id) if row.is_superseded else None
            ),
            unreadable_pages=row.unreadable_pages,
        )
        for row in rows
    ]


# Reciprocal Rank Fusion. 60 is the value from the original paper and the usual default;
# it damps the difference between ranks 1 and 2 so a single confident list cannot bully
# the other into irrelevance.
RRF_K = 60

# How deep each half looks before fusing. Wider than `limit` on purpose: a chunk the
# lexical side ranks 12th and the vector side ranks 30th should still be able to surface,
# and neither list can vote for what it never retrieved.
CANDIDATE_DEPTH = 50

_HYBRID_SQL = """
WITH searchable AS (
    SELECT c.id, c.embedding, c.content_tsv
    FROM chunks c
    JOIN document_versions v ON c.document_version_id = v.id
    WHERE v.status = ANY(CAST(:statuses AS version_status[]))
),
vector_hits AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:query_vector AS vector)) AS rank
    FROM searchable
    ORDER BY embedding <=> CAST(:query_vector AS vector)
    LIMIT :depth
),
lexical_hits AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(content_tsv, query) DESC) AS rank
    FROM searchable, websearch_to_tsquery(:config, :query_text) AS query
    WHERE content_tsv @@ query
    ORDER BY ts_rank_cd(content_tsv, query) DESC
    LIMIT :depth
)
SELECT
    COALESCE(v.id, l.id) AS chunk_id,
    COALESCE(1.0 / (:k + v.rank), 0.0) + COALESCE(1.0 / (:k + l.rank), 0.0) AS rrf_score,
    v.id IS NOT NULL AS found_by_vector,
    l.id IS NOT NULL AS found_by_lexical
FROM vector_hits v
FULL OUTER JOIN lexical_hits l ON v.id = l.id
ORDER BY rrf_score DESC
LIMIT :limit
"""


async def hybrid_search(
    session: AsyncSession,
    query_text: str,
    embedder: Embedder,
    *,
    limit: int = 10,
    include_archived: bool = False,
) -> list[SearchHit]:
    """Vector and lexical search, fused by rank (#15).

    Why this exists, measured rather than assumed. Asked about five ACE inhibitors whose
    passages differ only by drug name and dose, multilingual-e5-large gets top-1 right
    every time — but scores them 0.9126 / 0.8736 / 0.8693 / 0.8666 / 0.8585. All five
    inside 0.055. So any top-k above 1 hands back four passages about drugs nobody asked
    for, each looking as relevant as the right one, and #18 could lift lisinopril's dose
    into an answer about enalapril. Lexical search does not have opinions about
    near-misses: the word is there or it is not.

    Fused on rank, not score. Cosine distance and ts_rank_cd share no scale, and any
    mapping between them would be a constant somebody invented and nobody could audit
    later. RRF only asks each half for an ordering.

    websearch_to_tsquery, not to_tsquery: the input is a clinician's free text, and
    to_tsquery raises a syntax error on a stray quote or ampersand. A search box that
    500s on an apostrophe is not a search box.
    """
    # Expand before either half sees the text. The measurement said the vector half
    # needs this as much as the lexical one: a brand name is a fact the model was never
    # taught, not a token it quietly covers.
    expansion = expand_query(query_text, await load_aliases(session))

    embedding = embedder.embed_query(expansion.expanded)
    statuses = ["active", "archived"] if include_archived else ["active"]

    fused = (
        await session.execute(
            text(_HYBRID_SQL),
            {
                "statuses": statuses,
                "query_vector": str(embedding),
                "query_text": expansion.expanded,
                "config": TEXT_SEARCH_CONFIG,
                "depth": CANDIDATE_DEPTH,
                "k": RRF_K,
                "limit": limit,
            },
        )
    ).all()

    if not fused:
        return []

    ranking = {row.chunk_id: row for row in fused}

    detail = (
        await session.execute(
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
                DocumentVersion.unreadable_pages,
            )
            .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(Chunk.id.in_(list(ranking)))
        )
    ).all()

    labels = await latest_labels(session, [row.document_version_id for row in detail])

    hits = [
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
            superseding_version_label=(
                labels.get(row.document_version_id) if row.is_superseded else None
            ),
            unreadable_pages=row.unreadable_pages,
            found_by_vector=bool(ranking[row.id].found_by_vector),
            found_by_lexical=bool(ranking[row.id].found_by_lexical),
            rrf_score=float(ranking[row.id].rrf_score),
        )
        for row in detail
    ]

    # Ordered by the fusion, not by either half's own opinion.
    hits.sort(key=lambda hit: hit.rrf_score, reverse=True)
    return hits
