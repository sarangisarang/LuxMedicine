"""Indexing never holds the whole document's vectors at once.

The failure this pins. Indexing used to embed every chunk in one call, keep all the
vectors, build all the rows, and only then insert in slices. The slicing bounded the SQL
packet, not the memory — the full vector list already existed by then. On a 3.7 GB box the
kernel killed an ingest at anon-rss 3 486 504 kB (OOM, 2026-07-21 12:08:50) and the version
was simply left pending: an OOM kill leaves no traceback, no log line, nothing. The ingest
just did not work, with no way to tell that from a corrupt PDF or a bug.

Why a test and not a comment. The old shape is the *tidier* one — embed everything, then
write everything — and it reads as more efficient because it looks like one big call
instead of many. Someone will refactor it back, and nothing in the corpus, the suite, or
the logs would show it: a smaller corpus fits in memory either way, so the tests stay green
and the box only falls over on a document large enough to matter. That is the exact shape
of the silent failure this project keeps finding, so the invariant is asserted rather than
described.

What is asserted is the *interleaving*, not a byte count. Measuring real memory in a test
is flaky and machine-dependent; what actually bounds memory is that a write happens before
the next batch is embedded. If embedding and writing interleave, peak memory is one batch
whatever the document's size — which is the property, stated exactly.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.indexing import EMBED_BATCH, index_version

DIM = get_settings().embedding_dim


class RecordingEmbedder:
    """Notes the size of every embed call, and when each happened relative to the inserts.

    `events` is the interleaving: "embed" and "insert" in the order they really occurred.
    """

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.batch_sizes: list[int] = []

    @property
    def dimension(self) -> int:
        return DIM

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        self.batch_sizes.append(len(texts))
        self.events.append("embed")
        vector = [0.0] * DIM
        vector[7] = 1.0
        return [list(vector) for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        vector[7] = 1.0
        return vector


@pytest.fixture
async def long_version(session):
    """A version whose PDF yields comfortably more than one batch of chunks."""
    marker = uuid.uuid4().hex[:10]
    document = Document(
        title=f"Streaming Fixture {marker}", issuing_org="ESC", region="EU"
    )
    session.add(document)
    await session.flush()

    version = DocumentVersion(
        document_id=document.id,
        version_label="2026",
        file_hash=hashlib.sha256(marker.encode()).hexdigest(),
        storage_uri=f"{marker}.pdf",
        status=VersionStatus.PENDING,
    )
    session.add(version)
    await session.flush()
    return version


async def test_embedding_and_writing_interleave(session, long_version, monkeypatch):
    """The invariant: a batch is written before the next one is embedded.

    Concretely, the event stream must not be "every embed, then every insert". If it is,
    every vector was resident simultaneously and the document's size sets peak memory.
    """
    from app.services import indexing

    n_chunks = EMBED_BATCH * 2 + 5  # three batches, the last one partial
    events: list[str] = []

    _fake_document, chunks = _fake_extraction(n_chunks)
    monkeypatch.setattr(indexing, "extract_pdf", lambda path: _fake_document)
    monkeypatch.setattr(indexing, "chunk_document", lambda doc: chunks)

    original_execute = session.execute

    async def recording_execute(statement, *args, **kwargs):
        if args and isinstance(args[0], list):  # the executemany insert of a chunk batch
            events.append("insert")
        return await original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", recording_execute)

    embedder = RecordingEmbedder(events)
    result = await index_version(session, long_version.id, embedder)

    assert result.chunks_written == n_chunks

    # Three batches: 200, 200, 5.
    assert embedder.batch_sizes == [EMBED_BATCH, EMBED_BATCH, 5]

    # No batch may exceed the constant — that is what bounds the peak.
    assert max(embedder.batch_sizes) <= EMBED_BATCH

    embeds_and_inserts = [e for e in events if e in ("embed", "insert")]
    assert embeds_and_inserts == ["embed", "insert"] * 3, (
        f"embedding and writing did not interleave: {embeds_and_inserts}. "
        "If every embed happens before the first insert, all vectors are resident at once "
        "and peak memory scales with the document, not with EMBED_BATCH."
    )


async def test_all_chunks_are_still_written(session, long_version, monkeypatch):
    """Streaming must not lose the tail. An off-by-one in the slicing would drop the last
    partial batch, and the version would go active holding most of a document — the
    partial-index failure the module docstring exists to prevent, arriving by a new door."""
    from app.services import indexing

    n_chunks = EMBED_BATCH + 1  # one full batch plus a single straggler
    _fake_document, chunks = _fake_extraction(n_chunks)
    monkeypatch.setattr(indexing, "extract_pdf", lambda path: _fake_document)
    monkeypatch.setattr(indexing, "chunk_document", lambda doc: chunks)

    await index_version(session, long_version.id, RecordingEmbedder([]))
    await session.flush()

    stored = (
        await session.execute(
            select(Chunk.ordinal).where(Chunk.document_version_id == long_version.id)
        )
    ).scalars().all()

    assert len(stored) == n_chunks
    assert sorted(stored) == list(range(n_chunks)), "a chunk was dropped or duplicated"


def _fake_extraction(n_chunks: int):
    """An extracted document and its chunks, without touching a PDF.

    Text is per-chunk distinct so a duplicate write cannot hide behind identical content.
    """
    from app.services.chunking import TextChunk
    from app.services.extraction import ExtractedDocument, PageText

    body = "page one"
    document = ExtractedDocument(
        text=body,
        pages=[PageText(number=1, text=body, char_start=0, char_end=len(body))],
        lines=[],
        empty_pages=[],
        body_font_size=10.0,
    )
    chunks = [
        TextChunk(
            ordinal=i,
            text=f"chunk number {i} with enough words to look like real guidance",
            char_start=i * 10,
            char_end=i * 10 + 9,
            page_start=1,
            page_end=1,
            section=None,
        )
        for i in range(n_chunks)
    ]
    return document, chunks
