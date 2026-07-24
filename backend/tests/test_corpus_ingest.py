"""The folder-upload's unit of work: split an oversized PDF, then ingest each part end to end.

Unlike `test_pdf_split` (pure, in-memory), this drives the real thing — register, store, extract,
chunk, embed — against a text-bearing PDF so the assertion is that a document *becomes searchable*,
in as many parts as its size implies, each named the way the hand-built corpus is.
"""

from __future__ import annotations

import hashlib
import uuid
from io import BytesIO

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.vocabulary import IssuingOrg, LicenseStatus
from app.models.document import Document, DocumentVersion, VersionStatus
from app.models.chunk import Chunk
from app.services import storage
from app.services.corpus_ingest import ingest_document, ingest_pdf


class FakeEmbedder:
    """Deterministic, correctly-dimensioned vectors — the corpus path under test is the ingest
    wiring, not the embedding quality, and a real embedder loads ~1 GB and costs seconds."""

    @property
    def dimension(self) -> int:
        return get_settings().embedding_dim

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        dim = get_settings().embedding_dim
        return [digest[i % len(digest)] / 255.0 for i in range(dim)]


def _text_pdf(pages: int) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for i in range(pages):
        # Enough distinct prose per page that extraction yields at least one chunk.
        pdf.drawString(72, 720, f"Clinical guideline page {i + 1}.")
        pdf.drawString(72, 700, f"Recommendation {i + 1}: assess the patient and document findings.")
        pdf.drawString(72, 680, f"Therapy note {i + 1}: dosing and monitoring for the condition.")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


async def test_a_large_document_is_ingested_as_named_parts(session, tmp_path, monkeypatch):
    root = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_root", root)
    base = f"Long Guideline {uuid.uuid4().hex[:8]}"

    versions = await ingest_document(
        session,
        pdf=_text_pdf(5),
        title=base,
        org=IssuingOrg.CDC,
        version_label="2026",
        embedder=FakeEmbedder(),
        license_status=LicenseStatus.PUBLIC_DOMAIN,
        max_pages=2,  # 5 pages / 2 => 3 parts
    )

    assert [v.title for v in versions] == [
        f"{base} (part 01)",
        f"{base} (part 02)",
        f"{base} (part 03)",
    ]
    # Each part is a real, active, searchable version — not just a row.
    for v in versions:
        assert v.result.chunks_written >= 1
        stored = (
            await session.execute(
                select(DocumentVersion).where(DocumentVersion.id == v.version_id)
            )
        ).scalar_one()
        assert stored.status == VersionStatus.ACTIVE
        assert storage.resolve(stored.storage_uri, root=root).is_file()

    # Three distinct documents, each with its own chunks.
    titles = {
        t for (t,) in (
            await session.execute(select(Document.title).where(Document.title.like(f"{base}%")))
        ).all()
    }
    assert len(titles) == 3


async def test_a_small_document_is_one_version_with_the_plain_title(session, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path / "storage")
    base = f"Short Guideline {uuid.uuid4().hex[:8]}"
    versions = await ingest_document(
        session,
        pdf=_text_pdf(2),
        title=base,
        org=IssuingOrg.CDC,
        version_label="2026",
        embedder=FakeEmbedder(),
        license_status=LicenseStatus.PUBLIC_DOMAIN,
        max_pages=120,
    )
    assert len(versions) == 1
    assert versions[0].title == base  # no " (part NN)" when it was not split


async def test_an_affirmed_licence_goes_active_an_unaffirmed_one_is_quarantined(session, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path / "storage")

    affirmed = await ingest_document(
        session,
        pdf=_text_pdf(2),
        title=f"Affirmed {uuid.uuid4().hex[:8]}",
        org=IssuingOrg.CDC,
        version_label="2026",
        embedder=FakeEmbedder(),
        license_status=LicenseStatus.LICENSED,
    )
    assert affirmed[0].quarantined is False
    stored = (
        await session.execute(select(DocumentVersion).where(DocumentVersion.id == affirmed[0].version_id))
    ).scalar_one()
    assert stored.status == VersionStatus.ACTIVE
    assert stored.license_status == "licensed"

    # No licence affirmed (the default) — indexed, chunks present, but WITHDRAWN so retrieval never
    # sees it. This is the gate the 2026-07-24 withdrawal put up.
    quarantined = await ingest_document(
        session,
        pdf=_text_pdf(2),
        title=f"Unaffirmed {uuid.uuid4().hex[:8]}",
        org=IssuingOrg.CDC,
        version_label="2026",
        embedder=FakeEmbedder(),
        provenance_source="found it somewhere",
        # license_status omitted -> defaults to UNKNOWN
    )
    assert quarantined[0].quarantined is True
    assert quarantined[0].result.chunks_written >= 1  # it WAS indexed, just not made active
    stored_q = (
        await session.execute(select(DocumentVersion).where(DocumentVersion.id == quarantined[0].version_id))
    ).scalar_one()
    assert stored_q.status == VersionStatus.WITHDRAWN
    assert stored_q.license_status == "unknown"
    assert stored_q.provenance_source == "found it somewhere"


async def test_a_non_pdf_is_refused_before_anything_is_written(session, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path / "storage")
    with pytest.raises(storage.NotAPdfError):
        await ingest_pdf(
            session,
            pdf=b"this is not a pdf",
            title=f"junk {uuid.uuid4().hex[:8]}",
            org=IssuingOrg.CDC,
            version_label="2026",
            embedder=FakeEmbedder(),
        )
