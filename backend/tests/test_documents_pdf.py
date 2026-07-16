"""Serving a version's source PDF (#35), and the tenancy that guards it.

The file store is content-addressed and has no notion of a clinic — access is decided at
the row, under the same row-level security as everything else. So the load-bearing fact is
not "the handler streams a file", it is "the handler reads the version through the *tenant*
session, so a version another clinic owns is a 404". That is asserted two ways here:

  - structurally, that the endpoint depends on get_tenant_session and not the unscoped
    get_session — because a swap to the latter would bypass RLS and every logic test below
    would still pass (they run as the owner, which sees every row);
  - and the RLS filtering of the exact by-primary-key select this endpoint runs is proven
    in test_rls.py, at the app role where policies actually apply.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


async def _seed_version(session: AsyncSession, *, storage_uri: str) -> uuid.UUID:
    """A document and one active version pointing at `storage_uri`. Written as the owner —
    the tenancy under test is on the read path, not the fixture."""
    doc_id = uuid.uuid4()
    version_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO documents (id, title, issuing_org, region, clinic_id) "
            "VALUES (:id, :t, 'ESC', 'EU', NULL)"
        ),
        {"id": doc_id, "t": f"Guideline {uuid.uuid4().hex[:6]}"},
    )
    await session.execute(
        text(
            "INSERT INTO document_versions (id, document_id, version_label, file_hash, "
            "storage_uri, status) VALUES (:v, :d, '2024', :h, :u, 'active')"
        ),
        {"v": version_id, "d": doc_id, "h": uuid.uuid4().hex, "u": storage_uri},
    )
    await session.commit()
    return version_id


async def _get(session: AsyncSession, version_id) -> "tuple[int, bytes, dict]":
    """GET /documents/{id}/pdf with the tenant session overridden to the test session."""
    from app.db.session import get_tenant_session
    from app.main import app

    async def override():
        yield session

    app.dependency_overrides[get_tenant_session] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get(f"/documents/{version_id}/pdf")
            return r.status_code, r.content, dict(r.headers)
    finally:
        app.dependency_overrides.clear()


def test_the_pdf_endpoint_reads_through_the_tenant_session_not_an_unscoped_one():
    """The security wiring, asserted structurally so it survives a green logic suite.

    Every behavioural test below runs as the database owner, which bypasses RLS — so a
    handler that used get_session (unscoped) would serve another clinic's private PDF and
    all of them would still pass. This is what would fail instead: the dependency must be
    the tenant-bound session, the one test_rls.py proves isolates the by-id select.
    """
    import inspect

    from app.api import documents
    from app.db.session import get_session, get_tenant_session

    dependency = inspect.signature(documents.get_version_pdf).parameters[
        "session"
    ].default.dependency
    assert dependency is get_tenant_session
    assert dependency is not get_session


async def test_a_visible_version_streams_its_pdf_inline(session, tmp_path):
    pdf = tmp_path / "guideline.pdf"
    pdf.write_bytes(PDF_BYTES)
    version_id = await _seed_version(session, storage_uri=str(pdf))

    status, body, headers = await _get(session, version_id)

    assert status == 200
    assert headers["content-type"] == "application/pdf"
    # Inline, so the viewer shows the page in place rather than triggering a download.
    assert "inline" in headers.get("content-disposition", "")
    assert body == PDF_BYTES


async def test_an_unknown_version_is_404(session):
    status, _, _ = await _get(session, uuid.uuid4())
    assert status == 404


async def test_a_version_whose_file_is_missing_is_500_not_404(session, tmp_path):
    """The row is visible but the bytes are gone — our fault, not a missing document. A 404
    would send a clinician looking for a guideline we lost rather than one that never was."""
    gone = tmp_path / "not-here.pdf"  # never created
    version_id = await _seed_version(session, storage_uri=str(gone))

    status, _, _ = await _get(session, version_id)

    assert status == 500
