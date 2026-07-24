"""The upload endpoint's trust boundary and its wiring, over HTTP.

The security-critical claims: only a clinic-admin may upload, and a provenance affirmation is
required (the guard put up after the 2026-07-24 copyright withdrawal). The wiring claim: a POST
returns a job id at once and the background run reports status — exercised with a deliberately
unreadable "PDF", so the whole endpoint → background → status path runs without writing to the DB
(the real ingest is proven in test_corpus_ingest).
"""

from __future__ import annotations

import contextlib

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import Clinician, current_clinician
from app.core.config import get_settings


class _FakeEmbedder:
    @property
    def dimension(self) -> int:
        return get_settings().embedding_dim

    def embed_passages(self, texts):
        return [[0.0] * self.dimension for _ in texts]

    def embed_query(self, text):
        return [0.0] * self.dimension


@contextlib.asynccontextmanager
async def _client(session, *, clinician=None):
    from app.api.queries import get_embedder
    from app.db.session import get_session
    from app.main import app

    async def _session():
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_embedder] = lambda: _FakeEmbedder()
    if clinician is not None:
        app.dependency_overrides[current_clinician] = lambda: clinician
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _admin(clinic="clinic-north"):
    return Clinician(actor_id="admin-1", clinic_id=clinic, roles=frozenset({"clinic-admin"}))


def _plain(clinic="clinic-north"):
    return Clinician(actor_id="user-1", clinic_id=clinic, roles=frozenset())


_FORM = {
    "issuing_org": "CDC",
    "version_label": "2026",
    "license_status": "public_domain",
    "provenance_source": "US federal register",
}
_FILES = [("files", ("junk.pdf", b"not a pdf at all", "application/pdf"))]


@pytest.mark.asyncio
async def test_a_non_admin_cannot_upload(session):
    async with _client(session, clinician=_plain()) as client:
        r = await client.post("/ingest/batches", data=_FORM, files=_FILES)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_the_licence_gate_fields_are_required(session):
    async with _client(session, clinician=_admin()) as client:
        form = {"issuing_org": "CDC", "version_label": "2026"}  # no license_status / provenance_source
        r = await client.post("/ingest/batches", data=form, files=_FILES)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_empty_provenance_source_is_rejected(session):
    async with _client(session, clinician=_admin()) as client:
        form = {**_FORM, "provenance_source": ""}
        r = await client.post("/ingest/batches", data=form, files=_FILES)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_an_invalid_licence_status_is_rejected(session):
    async with _client(session, clinician=_admin()) as client:
        form = {**_FORM, "license_status": "totally-fine-trust-me"}
        r = await client.post("/ingest/batches", data=form, files=_FILES)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_an_admin_upload_is_accepted_and_reports_status(session):
    async with _client(session, clinician=_admin()) as client:
        r = await client.post("/ingest/batches", data=_FORM, files=_FILES)
        assert r.status_code == 202
        job_id = r.json()["job_id"]

        # httpx awaits the background task, so by now the unreadable file has been processed.
        status_r = await client.get(f"/ingest/batches/{job_id}")

    assert status_r.status_code == 200
    body = status_r.json()
    assert body["status"] == "failed"  # the garbage file could not be read; nothing was written
    assert body["percent"] == 100
    assert "could not read PDF" in body["files"][0]["error"]


@pytest.mark.asyncio
async def test_status_of_an_unknown_job_is_404(session):
    async with _client(session, clinician=_admin()) as client:
        r = await client.get("/ingest/batches/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a_non_admin_cannot_read_status(session):
    async with _client(session, clinician=_plain()) as client:
        r = await client.get("/ingest/batches/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 403
