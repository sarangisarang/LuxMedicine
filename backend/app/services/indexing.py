"""Turning a registered version into retrievable chunks (#10).

extract -> chunk -> embed -> insert -> activate. All in one transaction, on purpose.

A partial index is the failure this guards against. If embedding dies at chunk 400 of
600 and the first 400 stay committed, the version holds two thirds of a guideline and
nothing says so. Retrieval answers from it, confidently, with the missing third silently
absent — and the missing third is as likely as any other to hold the contraindication.
Either the whole guideline is searchable or none of it is.

Activation is the last statement. A version is only `active` once its chunks are in the
same committed transaction, so "reachable" and "complete" cannot come apart.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import DocumentVersion, VersionStatus
from app.services import storage
from app.services.chunking import chunk_document
from app.services.embedding import Embedder
from app.services.references import looks_like_reference
from app.services.extraction import UNRESOLVED_GLYPH, extract_pdf

# Chunks embedded, built into rows, and inserted per pass — the three steps stay together
# on purpose, because splitting them is what ran this box out of memory.
#
# It used to embed the whole document in one call, hold every vector, build every row, and
# only then insert in slices. The slicing bounded the *packet* and not the memory: the full
# vector list already existed. That is expensive in a way the numbers hide — `tolist()`
# returns 1024 Python floats per vector, and a Python float is a 24-byte object plus an
# 8-byte pointer in the list, so one vector is ~32 KB in RAM where float32 would be 4 KB.
# Add the prefixed copy of every text the embedder makes, plus the row dicts holding the
# text a second time, and a long document needs gigabytes with nothing written yet.
#
# Measured, not theorised: the kernel killed an ingest at anon-rss 3 486 504 kB on a 3.7 GB
# box (OOM, 2026-07-21 12:08:50), leaving the version pending with no error in any log —
# an OOM kill is silent by nature, so the failure looked like the ingest simply not working.
#
# Streaming makes peak memory a function of this constant instead of document length: about
# 200 * 32 KB ~ 6 MB of vectors alive at a time, whatever the size of the PDF.
#
# The single-transaction guarantee is untouched. These inserts are still one transaction and
# activation is still the last statement — a version is never half-indexed and reachable.
EMBED_BATCH = 200


class VersionNotPendingError(Exception):
    """Only a pending version can be indexed.

    Re-indexing an active version would duplicate its chunks, and retrieval would then
    read one guideline as two sources agreeing with each other — the same corrupted
    consensus that #7's deduplication exists to prevent, arriving by a different door.
    """

    def __init__(self, version_id: uuid.UUID, status: VersionStatus) -> None:
        super().__init__(f"version {version_id} is {status.value}, not pending")
        self.status = status


class EmptyExtractionError(Exception):
    """Text was extracted but produced no chunks."""


@dataclass(frozen=True)
class IndexResult:
    version_id: uuid.UUID
    chunks_written: int
    pages: int
    empty_pages: list[int]

    # Chunks dropped for carrying glyphs the font never named (#41). A count of zero and
    # a count of forty produce the same corpus from the outside — one just quietly has
    # less in it. Surfaced so an operator sees the difference before a clinician does.
    rejected_chunks: int = 0
    damaged_pages: list[int] = field(default_factory=list)


async def index_version(
    session: AsyncSession,
    version_id: uuid.UUID,
    embedder: Embedder,
) -> IndexResult:
    """Index one pending version. Caller commits.

    Raises NoTextLayerError (scanned PDF, #13), VersionNotPendingError, or
    EmptyExtractionError. Any of them leaves the version pending with no chunks, which
    is the correct resting state: unreachable rather than half-there.
    """
    version = (
        await session.execute(select(DocumentVersion).where(DocumentVersion.id == version_id))
    ).scalar_one()

    if version.status is not VersionStatus.PENDING:
        raise VersionNotPendingError(version_id, version.status)

    # Resolved against the configured root, not taken as an absolute path: the same row has
    # to find its bytes whether this runs on the host or in a container.
    document = extract_pdf(storage.resolve(version.storage_uri, root=get_settings().storage_root))
    chunks = chunk_document(document)

    if not chunks:
        raise EmptyExtractionError(f"version {version_id}: extraction produced no chunks")

    # #41. A chunk whose glyphs the font never named must not reach a clinician.
    #
    # Not because it is ugly — because it is *wrong* and nothing downstream can tell.
    # Measured on KDIGO 2012 CKD: "141(cid:2)min(SCr/k,1)a(cid:2)max(SCr/k,1)(cid:3)1.209"
    # is the CKD-EPI equation with every multiplication sign and the minus deleted. The
    # model is handed the chunk, never the PDF, so quoting that faithfully passes #19 —
    # the quote really is a substring of the chunk. #19 validates quotes against chunks;
    # this is the only place anything validates a chunk against its source.
    #
    # Dropped per chunk rather than per document: 18 damaged pages out of 163 is a real
    # guideline that mostly extracted cleanly, and refusing all of it loses 145 good pages.
    #
    # Dropped *loudly*, though. A silently skipped chunk and a silently written corrupt one
    # produce the same sentence for the clinician — "no guidance found" — and #20 exists
    # because those must never be the same output. The result carries what was dropped and
    # which pages it came from, so a human can open the PDF at that page and decide.
    kept, rejected = [], []
    for chunk in chunks:
        (rejected if UNRESOLVED_GLYPH.search(chunk.text) else kept).append(chunk)

    if not kept:
        raise EmptyExtractionError(
            f"version {version_id}: every chunk carries unresolved glyphs — the text layer "
            "names no characters for this font, so there is nothing here to quote (#41)"
        )

    chunks = kept

    # Embed, build, insert — one slice at a time, so nothing proportional to the document
    # is ever fully resident. See EMBED_BATCH on why this is not the same as batching the
    # insert alone.
    #
    # Still batched inside the embedder (16 per model pass), so this does not cost throughput:
    # a 200-chunk slice is 12 full passes and one partial, where feeding chunks one at a time
    # would waste most of every batch.
    written = 0
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        vectors = embedder.embed_passages([chunk.text for chunk in batch])

        if len(vectors) != len(batch):  # pragma: no cover — a broken embedder, not a bug here
            raise RuntimeError(f"embedder returned {len(vectors)} vectors for {len(batch)} chunks")

        await session.execute(
            insert(Chunk),
            [
                {
                    "id": uuid.uuid4(),
                    "document_version_id": version_id,
                    "ordinal": chunk.ordinal,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section": chunk.section,
                    "content": chunk.text,
                    "embedding": vector,
                    # A bibliography entry is embedded and stored like any other — a citation
                    # must still resolve to it — but flagged so retrieval never lets it source
                    # an answer (#50). Set here so every new document is clean; the backfill
                    # covers old ones.
                    "is_reference": looks_like_reference(chunk.text),
                }
                for chunk, vector in zip(batch, vectors, strict=True)
            ],
        )
        written += len(batch)

    # The damage, recorded on the version rather than returned and forgotten (#41, 0013).
    # The previous commit refused the corrupted chunks and told nobody, so the corpus just
    # got quieter — and "the guideline does not say" and "we could not read the page where
    # it says it" became the same answer, which is precisely what #20 exists to prevent.
    #
    # Written even when empty: [] means measured and clean, NULL means nobody looked.
    version.unreadable_pages = document.damaged_pages

    # Last, and inside the same transaction: reachable and complete stay coupled.
    version.status = VersionStatus.ACTIVE

    return IndexResult(
        version_id=version_id,
        chunks_written=written,
        pages=len(document.pages),
        empty_pages=document.empty_pages,
        rejected_chunks=len(rejected),
        damaged_pages=document.damaged_pages,
    )


async def count_chunks(session: AsyncSession, version_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.document_version_id == version_id)
        )
    ).scalar_one()
