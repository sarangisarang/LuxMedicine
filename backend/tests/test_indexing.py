"""Indexing pipeline tests (#10): extract -> chunk -> embed -> insert -> activate."""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import select

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.extraction import NoTextLayerError
from app.services.indexing import VersionNotPendingError, count_chunks, index_version

DIM = get_settings().embedding_dim


class FakeEmbedder:
    """Deterministic vectors, no model.

    Values are derived from the text so identical chunks embed identically — enough to
    exercise the pipeline. It says nothing about retrieval quality, which is #14's
    problem and needs the real model.
    """

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.fail_after = fail_after
        self.batches: list[list[str]] = []

    @property
    def dimension(self) -> int:
        return DIM

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        if self.fail_after is not None and len(texts) > self.fail_after:
            raise RuntimeError("embedding backend fell over")
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        seed = hashlib.sha256(text.encode()).digest()
        return [seed[i % len(seed)] / 255.0 for i in range(DIM)]


def build_guideline(path: Path, *, pages: int = 2) -> Path:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for page in range(pages):
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(60, 800, f"{page + 1} Section {page + 1}")
        pdf.setFont("Helvetica", 10)
        y = 775
        for i in range(20):
            pdf.drawString(60, y, f"Page {page + 1} sentence {i:02d} about clinical management.")
            y -= 14
        pdf.showPage()
    pdf.save()
    return path


@dataclass(frozen=True)
class PendingVersion:
    """Plain ids, not the ORM instance.

    Tests here roll back on purpose, and rollback expires ORM instances regardless of
    expire_on_commit — touching version.id afterwards would attempt lazy IO from a sync
    context and fail as MissingGreenlet rather than as whatever the test was checking.
    """

    id: uuid.UUID
    document_id: uuid.UUID
    path: Path


@pytest.fixture
async def pending_version(session, tmp_path) -> PendingVersion:
    """A registered version whose PDF is on disk and whose chunks do not exist yet.

    Titles are unique per test: the suite is additive by design (audit_log rejects
    TRUNCATE, so there is no reset), and uq_document_org_title would otherwise reject
    the second test's fixture.
    """
    pdf = build_guideline(tmp_path / "guideline.pdf")

    document = Document(
        title=f"Indexing Guideline {uuid.uuid4().hex[:8]}", issuing_org="ESC", region="EU"
    )
    session.add(document)
    await session.flush()

    version = DocumentVersion(
        document_id=document.id,
        version_label=f"v-{uuid.uuid4().hex[:8]}",
        file_hash=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        storage_uri=str(pdf),
    )
    session.add(version)
    await session.commit()
    return PendingVersion(id=version.id, document_id=document.id, path=pdf)


# --- the happy path ----------------------------------------------------------------


async def test_indexing_writes_chunks_and_activates_the_version(session, pending_version):
    result = await index_version(session, pending_version.id, FakeEmbedder())
    await session.commit()

    assert result.chunks_written > 0
    assert await count_chunks(session, pending_version.id) == result.chunks_written

    refreshed = (
        await session.execute(
            select(DocumentVersion).where(DocumentVersion.id == pending_version.id)
        )
    ).scalar_one()
    assert refreshed.status is VersionStatus.ACTIVE


async def test_chunks_carry_pages_sections_and_vectors(session, pending_version):
    await index_version(session, pending_version.id, FakeEmbedder())
    await session.commit()

    chunks = (
        await session.execute(
            select(Chunk).where(Chunk.document_version_id == pending_version.id).order_by(Chunk.ordinal)
        )
    ).scalars().all()

    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert len(chunk.embedding) == DIM
        assert chunk.page_end >= chunk.page_start
        assert chunk.content.strip()

    assert any(c.section for c in chunks), "section labels never reached the database"
    assert {c.page_start for c in chunks} == {1, 2}, "both pages should be represented"


async def test_the_embedder_receives_every_chunk_in_one_call(session, pending_version):
    """One call, batched inside the embedder. Feeding it chunk by chunk would leave
    almost every batch empty and make a long guideline crawl."""
    embedder = FakeEmbedder()
    result = await index_version(session, pending_version.id, embedder)
    await session.commit()

    assert len(embedder.batches) == 1
    assert len(embedder.batches[0]) == result.chunks_written


# --- atomicity ---------------------------------------------------------------------


async def test_a_failed_embedding_leaves_no_chunks_and_no_activation(session, pending_version):
    """The failure this pipeline exists to prevent.

    A version holding two thirds of a guideline answers confidently with the missing
    third silently absent — and the missing third is as likely as any other to hold the
    contraindication. Either all of it is searchable or none of it is.
    """
    with pytest.raises(RuntimeError):
        await index_version(session, pending_version.id, FakeEmbedder(fail_after=0))
    await session.rollback()

    assert await count_chunks(session, pending_version.id) == 0

    refreshed = (
        await session.execute(
            select(DocumentVersion).where(DocumentVersion.id == pending_version.id)
        )
    ).scalar_one()
    assert refreshed.status is VersionStatus.PENDING, "a failed index must not activate"


async def test_a_failure_partway_through_the_inserts_rolls_back_the_earlier_ones(
    session, pending_version, monkeypatch
):
    """The test above only proves a *pre-insert* failure writes nothing — the embedder
    raises before a single row exists, which the transaction never has to work for.

    This one is the real case: rows are already inserted when a later batch fails. If
    the earlier batches survived, the version would hold a fraction of a guideline.
    Batch size is forced to 1 so the boundary is crossed with the small fixture.

    The failure is injected on the second *call* rather than at index 1 of one call,
    because indexing now embeds and inserts per batch instead of embedding everything
    first (see indexing.EMBED_BATCH — the old shape held every vector in memory and was
    OOM-killed on a long document). With EMBED_BATCH forced to 1 each call receives a
    single text, so there is no index 1 to corrupt. Same scenario either way: batch one is
    committed to the transaction, batch two fails.
    """
    monkeypatch.setattr("app.services.indexing.EMBED_BATCH", 1)

    class BadVectorEmbedder(FakeEmbedder):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def embed_passages(self, texts: list[str]) -> list[list[float]]:
            vectors = super().embed_passages(texts)
            self.calls += 1
            if self.calls == 2:
                # Wrong dimension: Postgres rejects it, and by now batch one is already
                # inserted — which is the only reason this test says anything.
                return [[0.0] * (DIM - 1) for _ in vectors]
            return vectors

    with pytest.raises(Exception):  # noqa: B017 — asyncpg wraps the dimension error
        await index_version(session, pending_version.id, BadVectorEmbedder())
    await session.rollback()

    assert await count_chunks(session, pending_version.id) == 0, (
        "chunks inserted before the failure survived — the version now holds part of a guideline"
    )

    refreshed = (
        await session.execute(
            select(DocumentVersion).where(DocumentVersion.id == pending_version.id)
        )
    ).scalar_one()
    assert refreshed.status is VersionStatus.PENDING


async def test_a_scanned_pdf_leaves_the_version_pending(session, tmp_path):
    """#13's signal, end to end: unreadable stays unreachable rather than becoming an
    active version with nothing in it."""
    path = tmp_path / "scanned.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.rect(100, 100, 200, 200, fill=1)
    pdf.showPage()
    pdf.save()

    document = Document(
        title=f"Scanned Guideline {uuid.uuid4().hex[:8]}", issuing_org="AHA", region="US"
    )
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_label=f"scan-{uuid.uuid4().hex[:8]}",
        file_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        storage_uri=str(path),
    )
    session.add(version)
    await session.commit()
    version_id = version.id  # rollback below expires the instance

    with pytest.raises(NoTextLayerError):
        await index_version(session, version_id, FakeEmbedder())
    await session.rollback()

    assert await count_chunks(session, version_id) == 0
    refreshed = (
        await session.execute(select(DocumentVersion).where(DocumentVersion.id == version_id))
    ).scalar_one()
    assert refreshed.status is VersionStatus.PENDING


# --- re-indexing -------------------------------------------------------------------


async def test_an_active_version_cannot_be_indexed_again(session, pending_version):
    """Re-indexing would duplicate the chunks, and retrieval would then read one
    guideline as two sources agreeing with each other — #7's corrupted consensus,
    arriving through a different door."""
    await index_version(session, pending_version.id, FakeEmbedder())
    await session.commit()
    before = await count_chunks(session, pending_version.id)

    with pytest.raises(VersionNotPendingError) as exc:
        await index_version(session, pending_version.id, FakeEmbedder())
    assert exc.value.status is VersionStatus.ACTIVE

    await session.rollback()
    assert await count_chunks(session, pending_version.id) == before
