"""Ingestion gate tests (#7).

The point of this gate is not tidiness. Ingesting a guideline twice doubles every
passage in the vector store, so retrieval surfaces one source as two — and #23 then
reads a single guideline as two organisations agreeing with each other. A deduplication
bug here becomes a fabricated consensus downstream.
"""

import asyncio
import hashlib
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import get_session
from app.main import app
from app.models.document import Document, DocumentVersion


def pdf_bytes(marker: str = "esc-2021") -> bytes:
    """Minimal bytes that pass the magic-number check. Real parsing starts at #8."""
    return b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + marker.encode() + b"\n%%EOF\n"


@pytest.fixture
async def client(engine, tmp_path, monkeypatch):
    """App wired to the throwaway database and a temp storage root.

    Storage is redirected per-test so a passing suite never writes into the developer's
    real ./storage, and so each test starts with an empty store.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def upload_form(label: str = "2021", title: str = "Heart Failure Guidelines", org: str = "ESC"):
    return {
        "title": title,
        "issuing_org": org,
        "version_label": label,
        "guideline_type": "clinical practice guideline",
    }


async def post_pdf(client, content: bytes, **form):
    return await client.post(
        "/documents/versions",
        files={"file": ("guideline.pdf", content, "application/pdf")},
        data=upload_form(**form),
    )


# --- the happy path ----------------------------------------------------------------


async def test_upload_registers_document_and_version(client, session):
    body = pdf_bytes("esc-hf-2021")
    response = await post_pdf(client, body)

    assert response.status_code == 201, response.text
    payload = response.json()

    assert payload["file_hash"] == hashlib.sha256(body).hexdigest()
    assert payload["size_bytes"] == len(body)

    version = (
        await session.execute(
            select(DocumentVersion).where(DocumentVersion.id == uuid.UUID(payload["document_version_id"]))
        )
    ).scalar_one()
    assert version.status.value == "active"
    assert version.superseded_by is None


async def test_the_pdf_is_stored_at_its_content_address(client, session):
    body = pdf_bytes("stored-bytes")
    response = await post_pdf(client, body, label="2022", title="Stored Guideline")
    assert response.status_code == 201

    version = (
        await session.execute(
            select(DocumentVersion).where(
                DocumentVersion.id == uuid.UUID(response.json()["document_version_id"])
            )
        )
    ).scalar_one()

    stored = Path(version.storage_uri)
    assert stored.exists(), "hashing the bytes and discarding them would prove nothing later"
    assert stored.read_bytes() == body, "the stored file must be the bytes we hashed"
    assert version.file_hash in stored.name


async def test_region_defaults_from_the_issuing_org(client, session):
    response = await post_pdf(client, pdf_bytes("aha-1"), title="AHA Guideline", org="AHA")
    assert response.status_code == 201

    document = (
        await session.execute(
            select(Document).where(Document.id == uuid.UUID(response.json()["document_id"]))
        )
    ).scalar_one()
    assert document.region == "US"


# --- deduplication -----------------------------------------------------------------


async def test_identical_bytes_are_rejected(client):
    body = pdf_bytes("dedup-me")
    first = await post_pdf(client, body, label="2021", title="Dedup Guideline")
    assert first.status_code == 201

    # Same bytes, different metadata — the content is what identifies it.
    second = await post_pdf(client, body, label="2023", title="Dedup Guideline")
    assert second.status_code == 409

    detail = second.json()["detail"]
    assert detail["reason"] == "duplicate_file"
    assert detail["existing_version_id"] == first.json()["document_version_id"]
    assert detail["existing_version_label"] == "2021", "the caller needs to know where it already lives"


async def test_concurrent_uploads_of_the_same_file_yield_exactly_one_version(client, session):
    """The pre-check is an optimisation; the unique constraint is the guarantee.

    Both requests read 'no such hash' before either inserts, so without the
    IntegrityError path this produces either a 500 or — worse — two versions of one
    guideline, which is the duplicate-corpus problem the gate exists to prevent.
    """
    body = pdf_bytes("race-me")

    results = await asyncio.gather(
        *(post_pdf(client, body, label=f"v{i}", title="Race Guideline") for i in range(6))
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409, 409, 409, 409], f"got {codes}"

    stored = (
        await session.execute(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.file_hash == hashlib.sha256(body).hexdigest())
        )
    ).scalar_one()
    assert stored == 1


async def test_reusing_a_version_label_with_different_bytes_is_rejected(client):
    """Distinct from a duplicate file, and more dangerous: it would silently change
    what an earlier answer cited."""
    assert (await post_pdf(client, pdf_bytes("v1"), label="2021", title="Label Guideline")).status_code == 201

    clash = await post_pdf(client, pdf_bytes("v2-different-bytes"), label="2021", title="Label Guideline")
    assert clash.status_code == 409
    assert clash.json()["detail"]["reason"] == "version_label_taken"


async def test_a_second_edition_reuses_the_same_document(client, session):
    """Both editions must hang off one document, or supersession (#12) marks the wrong
    predecessor and the staleness warning (#17) goes quiet."""
    first = await post_pdf(client, pdf_bytes("ed-2021"), label="2021", title="Evolving Guideline")
    second = await post_pdf(client, pdf_bytes("ed-2023"), label="2023", title="Evolving Guideline")
    assert first.status_code == 201 and second.status_code == 201

    assert first.json()["document_id"] == second.json()["document_id"]

    documents = (
        await session.execute(
            select(func.count()).select_from(Document).where(Document.title == "Evolving Guideline")
        )
    ).scalar_one()
    assert documents == 1


# --- rejection ---------------------------------------------------------------------


async def test_non_pdf_is_rejected_regardless_of_content_type(client, tmp_path):
    """Content-Type is whatever the client typed. The magic bytes are the file."""
    response = await client.post(
        "/documents/versions",
        files={"file": ("evil.pdf", b"PK\x03\x04 this is a zip", "application/pdf")},
        data=upload_form(),
    )
    assert response.status_code == 415


async def test_empty_file_is_rejected(client):
    response = await client.post(
        "/documents/versions",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        data=upload_form(),
    )
    assert response.status_code == 415


async def test_oversized_upload_is_rejected(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_bytes", 100)

    response = await post_pdf(client, pdf_bytes("x" * 500), label="big", title="Big Guideline")
    assert response.status_code == 413


async def test_unknown_issuing_org_is_rejected(client):
    """The controlled vocabulary is the whole defence against 'ESC' and 'esc' being
    read as two organisations that happen to agree."""
    response = await client.post(
        "/documents/versions",
        files={"file": ("g.pdf", pdf_bytes("unknown-org"), "application/pdf")},
        data=upload_form(org="Some Random Clinic"),
    )
    assert response.status_code == 422


# --- no orphans --------------------------------------------------------------------


async def test_a_rejected_upload_leaves_nothing_in_the_store(client, tmp_path):
    """A store holding PDFs no version points at is a data-retention problem wearing a
    disk-usage costume — under GDPR, files nobody can find are files nobody can erase."""
    body = pdf_bytes("orphan-check")
    assert (await post_pdf(client, body, label="2021", title="Orphan Guideline")).status_code == 201

    before = sorted(p.name for p in (tmp_path / "storage").rglob("*.pdf"))
    assert (await post_pdf(client, body, label="2023", title="Orphan Guideline")).status_code == 409
    after = sorted(p.name for p in (tmp_path / "storage").rglob("*.pdf"))

    assert before == after
    assert not list((tmp_path / "storage").rglob("*.part")), "a staged temp file was left behind"
