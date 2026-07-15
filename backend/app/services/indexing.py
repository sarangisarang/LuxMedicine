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
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import DocumentVersion, VersionStatus
from app.services.chunking import chunk_document
from app.services.embedding import Embedder
from app.services.extraction import extract_pdf

# Rows per executemany. Vectors are 1024 floats each, so a whole guideline in one
# statement makes for a very large packet; batching keeps memory and the wire sane
# without giving up the single transaction.
INSERT_BATCH = 200


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

    document = extract_pdf(Path(version.storage_uri))
    chunks = chunk_document(document)

    if not chunks:
        raise EmptyExtractionError(f"version {version_id}: extraction produced no chunks")

    # One call, batched internally by the embedder. Passing every chunk at once lets it
    # pack full batches; feeding it chunk by chunk would waste most of each one.
    vectors = embedder.embed_passages([chunk.text for chunk in chunks])

    if len(vectors) != len(chunks):  # pragma: no cover — a broken embedder, not a bug here
        raise RuntimeError(f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks")

    rows = [
        {
            "id": uuid.uuid4(),
            "document_version_id": version_id,
            "ordinal": chunk.ordinal,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "section": chunk.section,
            "content": chunk.text,
            "embedding": vector,
        }
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]

    for start in range(0, len(rows), INSERT_BATCH):
        await session.execute(insert(Chunk), rows[start : start + INSERT_BATCH])

    # Last, and inside the same transaction: reachable and complete stay coupled.
    version.status = VersionStatus.ACTIVE

    return IndexResult(
        version_id=version_id,
        chunks_written=len(rows),
        pages=len(document.pages),
        empty_pages=document.empty_pages,
    )


async def count_chunks(session: AsyncSession, version_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.document_version_id == version_id)
        )
    ).scalar_one()
