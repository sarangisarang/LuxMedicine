"""Self-service corpus upload: a clinic-admin uploads PDFs, the server ingests them in the
background, and the page polls for a 0-100% bar.

Three controls, because this is the one clinician-reachable path that WRITES to the corpus:
- **clinic-admin only** (`require_clinic_admin`) — same trust boundary as minting invites.
- **a licence gate** — a required `license_status` (public_domain / licensed / unknown) plus a
  non-empty `provenance_source`. On 2026-07-24 this project withdrew 10k+ chunks of copyrighted
  commercial PDFs (§19a UrhG); a frictionless upload re-opens exactly that exposure. Only an
  affirmed licence lets an upload go ACTIVE — anything else is ingested but QUARANTINED
  (WITHDRAWN, not retrievable) until a human confirms it. See LicenseStatus / corpus_ingest.
- **scoped to the admin's own clinic** (`clinic_id = admin.clinic_id`) — an upload is searchable by
  that clinic, not silently added to every clinic's global corpus.

Ingestion runs as a background task (indexing takes minutes and loads the embedder), so the POST
returns a job id at once and `GET /ingest/batches/{id}` reports phase + percent. The job store is
in-memory (see ingest_jobs) — correct for the single api process, a hardening follow-up for more.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.queries import get_embedder
from app.core.auth import Clinician, require_clinic_admin
from app.core.vocabulary import IssuingOrg, LicenseStatus
from app.db.session import SessionLocal
from app.services.corpus_ingest import ingest_pdf
from app.services.embedding import Embedder
from app.services.ingest_jobs import IngestJob, JobRegistry, JobStatus, run_batch
from app.services.pdf_split import PdfPart

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

# One store for the process. A dict is the right shape for a single uvicorn worker; the tradeoff
# (a restart forgets in-flight jobs, never committed work) is documented in ingest_jobs.
_registry = JobRegistry()


class FileStatus(BaseModel):
    filename: str
    parts: int
    done_parts: int
    version_ids: list[uuid.UUID]
    error: str | None


class BatchStatus(BaseModel):
    job_id: uuid.UUID
    sector: str
    status: JobStatus
    phase: str
    percent: int
    files: list[FileStatus]
    error: str | None


class BatchAccepted(BaseModel):
    job_id: uuid.UUID


def _to_status(job: IngestJob) -> BatchStatus:
    return BatchStatus(
        job_id=job.id,
        sector=job.sector.value,
        status=job.status,
        phase=job.phase,
        percent=job.percent,
        files=[
            FileStatus(
                filename=f.filename,
                parts=f.parts,
                done_parts=f.done_parts,
                version_ids=f.version_ids,
                error=f.error,
            )
            for f in job.files
        ],
        error=job.error,
    )


async def _run_guarded(job_id: uuid.UUID, uploads: list[tuple[str, bytes]], ingest_part) -> None:
    """run_batch already records per-file failures; this only catches an unexpected failure OUTSIDE
    the per-file loop (a bug, a lost DB) so the bar resolves to FAILED instead of hanging forever."""
    try:
        await run_batch(_registry, job_id, uploads, ingest_part=ingest_part)
    except Exception as exc:  # noqa: BLE001 — a background task's exception is otherwise swallowed
        logger.exception("ingest batch %s crashed", job_id)
        await _registry.set_status(
            job_id, status=JobStatus.FAILED, phase="failed", percent=100, error=str(exc)
        )


@router.post("/batches", response_model=BatchAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_batch(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    issuing_org: IssuingOrg = Form(...),
    version_label: str = Form(..., max_length=64),
    license_status: LicenseStatus = Form(...),
    provenance_source: str = Form(..., min_length=1, max_length=500),
    published_at: date | None = Form(None),
    guideline_type: str | None = Form(None, max_length=128),
    admin: Clinician = Depends(require_clinic_admin),
    embedder: Embedder = Depends(get_embedder),
) -> BatchAccepted:
    """Accept a folder of PDFs and start ingesting them. Returns a job id to poll.

    Each PDF is split if it exceeds 120 pages and every part is registered, stored, extracted,
    chunked and embedded — the whole `app.cli.ingest` flow, per part. The sector is derived from
    the issuing organisation, never chosen separately (the sector-drift discipline).
    """
    uploads = [(f.filename or "upload.pdf", await f.read()) for f in files]
    if not uploads:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no files in the upload")

    job = await _registry.create(
        sector=issuing_org.sector, filenames=[name for name, _ in uploads]
    )
    logger.info(
        "ingest batch %s: %d file(s), org=%s, clinic=%s, by=%s, license=%s, source=%r",
        job.id, len(uploads), issuing_org.value, admin.clinic_id, admin.actor_id,
        license_status.value, provenance_source,
    )

    async def ingest_part(title: str, part: PdfPart) -> uuid.UUID:
        # A fresh session per part: this runs after the response, so the request's session is gone.
        async with SessionLocal() as session:
            result = await ingest_pdf(
                session,
                pdf=part.data,
                title=title,
                org=issuing_org,
                version_label=version_label,
                embedder=embedder,
                license_status=license_status,
                provenance_source=provenance_source,
                published_at=published_at,
                guideline_type=guideline_type,
                clinic_id=admin.clinic_id,
            )
            return result.version_id

    background_tasks.add_task(_run_guarded, job.id, uploads, ingest_part)
    return BatchAccepted(job_id=job.id)


@router.get("/batches/{job_id}", response_model=BatchStatus)
async def batch_status(
    job_id: uuid.UUID,
    admin: Clinician = Depends(require_clinic_admin),
) -> BatchStatus:
    job = await _registry.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such ingest job")
    return _to_status(job)
