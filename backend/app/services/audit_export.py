"""Reconstructing what a clinician was told, and against what (#27).

The question a dispute asks is: *who asked what, when, and which edition and page did the
system rely on.* Everything needed is already recorded — this resolves
`retrieved_chunk_ids` through `chunks` to `document_versions` and renders it as one
answerable object.

Three things this does that a naive dump would not:

**It re-checks the quotes against the corpus.** The hash chain proves the audit rows were
not tampered with. It proves *nothing* about the guidelines — someone with database
access can edit a chunk's text and every chain verification still passes, because the
chain never hashed the corpus. The stored answer carries frozen quotes; the chunks are
live. Re-running #19's check at export time closes that gap: a quote that no longer
appears in the chunk it cited means the corpus moved under the trail.

**It reports chunks it cannot resolve.** `retrieved_chunk_ids` has no foreign key, and
deleting a document cascades to its chunks — so an old row can name sources that no
longer exist. Omitting them silently would make an incomplete export look complete, in
the one document whose entire purpose is completeness.

**It refuses to look clean when the chain is broken.** An export drawn from a tampered
trail is worse than no export: it carries our formatting and our authority. The
verification result travels with the entries, not beside them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion
from app.models.query import Query
from app.services.audit import ChainBreak, verify_chain
from app.services.validation import normalise


@dataclass(frozen=True)
class ExportedSource:
    """One passage the system read, as it can be located today."""

    chunk_id: uuid.UUID
    document_version_id: uuid.UUID
    document_title: str
    issuing_org: str
    version_label: str
    version_status: str
    page_start: int
    page_end: int
    section: str | None
    content: str

    # Where the original PDF is. A clinician or an expert can open it at `page_start` and
    # read the passage in its own context — which is the only check that catches an
    # extraction fault (#8, #13), since #19 cannot.
    storage_uri: str


@dataclass(frozen=True)
class QuoteIntegrity:
    """A quote from the stored answer, re-checked against the chunk it cited."""

    chunk_id: uuid.UUID
    quote: str
    still_matches: bool
    reason: str | None = None


@dataclass(frozen=True)
class ExportedEntry:
    seq: int
    recorded_at: datetime
    actor_id: str
    query_id: uuid.UUID

    # None once erased under GDPR. The hash below still proves *which* question was
    # asked when presented with a candidate — that is the whole point of hashing it
    # rather than retaining it (#5).
    question: str | None
    question_hash: str
    redacted_at: datetime | None

    model: str
    prompt_hash: str
    error: str | None

    # Everything retrieved, not only what was quoted. "What did the system look at" is
    # the question; the answer must include the passages it read and discarded.
    sources: list[ExportedSource]

    # Named in the row, absent from the database. See the module docstring.
    unresolvable_chunk_ids: list[uuid.UUID]

    response: dict
    quote_integrity: list[QuoteIntegrity]

    prev_hash: str
    row_hash: str

    @property
    def question_is_erased(self) -> bool:
        return self.redacted_at is not None

    @property
    def corpus_matches_the_record(self) -> bool:
        """Every quote we showed still appears in the passage it cited.

        Narrower than it sounds, and deliberately: a passage the system *read but did not
        quote* can vanish without this noticing, because there is no quote to check. That
        is `sources_complete`'s job — the two failures are different and a reader deserves
        to know which one happened.
        """
        return all(check.still_matches for check in self.quote_integrity)

    @property
    def sources_complete(self) -> bool:
        """Every passage the system read can still be produced."""
        return not self.unresolvable_chunk_ids


@dataclass(frozen=True)
class AuditExport:
    entries: list[ExportedEntry] = field(default_factory=list)

    # The chain, verified end to end — not just across the exported range. A range can be
    # internally consistent while the rows before it were rewritten, and the export would
    # look fine.
    chain_intact: bool = True
    chain_break: str | None = None

    @property
    def is_evidential(self) -> bool:
        """Whether this export can be relied on at all.

        False if the chain is broken, if a quote no longer matches its source, or if a
        passage the system read can no longer be produced. All three mean something moved
        that was supposed to be immovable, and none is a detail to bury in a footnote.
        """
        return (
            self.chain_intact
            and all(e.corpus_matches_the_record for e in self.entries)
            and all(e.sources_complete for e in self.entries)
        )


async def export_audit(
    session: AsyncSession,
    *,
    actor_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> AuditExport:
    """Reconstruct the trail for an actor and/or a time range.

    Read-only. Verifies the chain first: if it is broken there is no point rendering
    entries that may have been rewritten, and saying so is the honest output.
    """
    chain_intact = True
    chain_break: str | None = None
    try:
        await verify_chain(session)
    except ChainBreak as exc:
        chain_intact = False
        chain_break = str(exc)

    statement = select(AuditLog).order_by(AuditLog.seq)
    if actor_id is not None:
        statement = statement.where(AuditLog.actor_id == actor_id)
    if since is not None:
        statement = statement.where(AuditLog.created_at >= since)
    if until is not None:
        statement = statement.where(AuditLog.created_at <= until)

    rows = (await session.execute(statement)).scalars().all()
    if not rows:
        return AuditExport(chain_intact=chain_intact, chain_break=chain_break)

    queries = await _load_queries(session, [row.query_id for row in rows])
    chunks = await _load_chunks(session, {cid for row in rows for cid in row.retrieved_chunk_ids})

    entries = [_render(row, queries, chunks) for row in rows]
    return AuditExport(entries=entries, chain_intact=chain_intact, chain_break=chain_break)


async def _load_queries(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, Query]:
    rows = (await session.execute(select(Query).where(Query.id.in_(ids)))).scalars().all()
    return {row.id: row for row in rows}


async def _load_chunks(
    session: AsyncSession, ids: set[uuid.UUID]
) -> dict[uuid.UUID, ExportedSource]:
    if not ids:
        return {}

    rows = (
        await session.execute(
            select(
                Chunk.id,
                Chunk.document_version_id,
                Chunk.page_start,
                Chunk.page_end,
                Chunk.section,
                Chunk.content,
                Document.title,
                Document.issuing_org,
                DocumentVersion.version_label,
                DocumentVersion.status,
                DocumentVersion.storage_uri,
            )
            .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(Chunk.id.in_(ids))
        )
    ).all()

    return {
        row.id: ExportedSource(
            chunk_id=row.id,
            document_version_id=row.document_version_id,
            document_title=row.title,
            issuing_org=row.issuing_org,
            version_label=row.version_label,
            version_status=row.status.value,
            page_start=row.page_start,
            page_end=row.page_end,
            section=row.section,
            content=row.content,
            storage_uri=row.storage_uri,
        )
        for row in rows
    }


def _check_quotes(response: dict, chunks: dict[uuid.UUID, ExportedSource]) -> list[QuoteIntegrity]:
    """Re-run #19 against the corpus as it stands now.

    The stored answer is frozen by the chain; the chunks are not. A mismatch means the
    guideline text changed after the answer was given — which the chain cannot see,
    because it never hashed the corpus.
    """
    checks: list[QuoteIntegrity] = []

    for group in response.get("groups", []):
        for citation in group.get("citations", []):
            chunk_id = uuid.UUID(citation["chunk_id"])
            quote = citation["quote"]
            source = chunks.get(chunk_id)

            if source is None:
                checks.append(
                    QuoteIntegrity(
                        chunk_id=chunk_id,
                        quote=quote,
                        still_matches=False,
                        reason="the cited passage no longer exists",
                    )
                )
                continue

            if normalise(quote) not in normalise(source.content):
                checks.append(
                    QuoteIntegrity(
                        chunk_id=chunk_id,
                        quote=quote,
                        still_matches=False,
                        reason="the passage no longer contains this quote — the corpus changed",
                    )
                )
                continue

            checks.append(QuoteIntegrity(chunk_id=chunk_id, quote=quote, still_matches=True))

    return checks


def _render(
    row: AuditLog,
    queries: dict[uuid.UUID, Query],
    chunks: dict[uuid.UUID, ExportedSource],
) -> ExportedEntry:
    query = queries.get(row.query_id)

    sources = [chunks[cid] for cid in row.retrieved_chunk_ids if cid in chunks]
    missing = [cid for cid in row.retrieved_chunk_ids if cid not in chunks]

    return ExportedEntry(
        seq=row.seq,
        recorded_at=row.created_at,
        actor_id=row.actor_id,
        query_id=row.query_id,
        question=query.text if query else None,
        question_hash=row.query_hash,
        redacted_at=query.redacted_at if query else None,
        model=row.model,
        prompt_hash=row.prompt_hash,
        error=row.error,
        sources=sources,
        unresolvable_chunk_ids=missing,
        response=row.response,
        quote_integrity=_check_quotes(row.response, chunks),
        prev_hash=row.prev_hash,
        row_hash=row.row_hash,
    )
