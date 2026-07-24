"""Track a folder upload while it ingests, so the page can draw a 0-100% bar.

Indexing takes minutes and loads the embedder, so the upload endpoint cannot do it inside the
request (see corpus_ingest / ingest.py). It returns a job id, this runs the work in the background,
and the frontend polls the job for a phase and a percent.

**In-memory, single-process, on purpose — and honest about the cost.** The api runs as one uvicorn
process, so a dict is a correct store for it; a restart loses the *job* (the bar), not the *work* —
every part commits as it is indexed, and re-uploading is idempotent (identical bytes are refused by
`file_hash`). A DB-backed job table is the hardening follow-up when this grows past a demo; it is
not needed to make the bar correct today.

The runner is pure orchestration: it splits each file, then ingests each part through an injected
`ingest_part`, updating progress after each. Decoupling the per-part work is what lets the progress
logic — the phases, the percent, one bad file not sinking the batch — be tested without a database.
"""

from __future__ import annotations

import asyncio
import copy
import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from app.core.vocabulary import Sector
from app.services.pdf_split import DEFAULT_MAX_PAGES, PdfPart, split_if_large


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class FileProgress:
    """One uploaded PDF: how many parts it became, how many are in, and why it failed if it did.

    A file's failure is recorded here and does not stop the batch — one unreadable PDF in a folder
    of forty should cost that PDF, not the other thirty-nine."""

    filename: str
    parts: int = 0
    done_parts: int = 0
    version_ids: list[uuid.UUID] = field(default_factory=list)
    error: str | None = None


@dataclass
class IngestJob:
    id: uuid.UUID
    sector: Sector
    status: JobStatus = JobStatus.QUEUED
    phase: str = "queued"
    percent: int = 0
    files: list[FileProgress] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# The per-part unit of work the runner is given, so it never touches a session or the embedder
# directly: (title, part) -> the new version's id. Raises to signal the part failed.
IngestPart = Callable[[str, PdfPart], Awaitable[uuid.UUID]]

# Percent budget: a little for splitting, the bulk for indexing, which is where the minutes go.
_SPLIT_CEILING = 10


class JobRegistry:
    """A lock-guarded dict of jobs. `get` returns a deep copy so a poller never serialises a job
    that the runner is mutating underneath it."""

    def __init__(self) -> None:
        self._jobs: dict[uuid.UUID, IngestJob] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, sector: Sector, filenames: list[str]) -> IngestJob:
        job = IngestJob(
            id=uuid.uuid4(),
            sector=sector,
            files=[FileProgress(filename=name) for name in filenames],
        )
        async with self._lock:
            self._jobs[job.id] = job
        return copy.deepcopy(job)

    async def get(self, job_id: uuid.UUID) -> IngestJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job is not None else None

    async def _mutate(self, job_id: uuid.UUID, fn: Callable[[IngestJob], None]) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            fn(job)
            job.updated_at = datetime.now(timezone.utc)

    async def set_status(self, job_id: uuid.UUID, **fields: object) -> None:
        def apply(job: IngestJob) -> None:
            for key, value in fields.items():
                setattr(job, key, value)

        await self._mutate(job_id, apply)

    async def set_parts(self, job_id: uuid.UUID, file_index: int, parts: int) -> None:
        await self._mutate(job_id, lambda job: setattr(job.files[file_index], "parts", parts))

    async def part_done(self, job_id: uuid.UUID, file_index: int, version_id: uuid.UUID) -> None:
        def apply(job: IngestJob) -> None:
            f = job.files[file_index]
            f.version_ids.append(version_id)
            f.done_parts += 1

        await self._mutate(job_id, apply)

    async def file_failed(self, job_id: uuid.UUID, file_index: int, error: str) -> None:
        await self._mutate(job_id, lambda job: setattr(job.files[file_index], "error", error))


async def run_batch(
    registry: JobRegistry,
    job_id: uuid.UUID,
    uploads: list[tuple[str, bytes]],
    *,
    ingest_part: IngestPart,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> None:
    """Split every upload, ingest every part through `ingest_part`, and move the job 0 -> 100.

    `uploads` is (filename, bytes). The base title is the filename without extension, with a
    " (part NN)" suffix for a split document. A file that fails to split or index records its error
    and is skipped; the batch is DONE if anything landed and FAILED only if nothing did.
    """
    await registry.set_status(job_id, status=JobStatus.RUNNING, phase="splitting", percent=2)

    planned: list[tuple[int, str, PdfPart]] = []
    for index, (filename, data) in enumerate(uploads):
        try:
            parts = split_if_large(data, max_pages=max_pages)
        except Exception as exc:  # noqa: BLE001 — one unreadable file must not sink the batch
            await registry.file_failed(job_id, index, f"could not read PDF: {exc}")
            continue
        await registry.set_parts(job_id, index, len(parts))
        base = Path(filename).stem
        for part in parts:
            planned.append((index, f"{base}{part.label_suffix}", part))

    total = len(planned)
    if total == 0:
        await registry.set_status(
            job_id,
            status=JobStatus.FAILED,
            phase="failed",
            percent=100,
            error="no readable PDF in the upload",
        )
        return

    await registry.set_status(job_id, phase="indexing", percent=_SPLIT_CEILING)
    done = 0
    for index, title, part in planned:
        try:
            version_id = await ingest_part(title, part)
            await registry.part_done(job_id, index, version_id)
        except Exception as exc:  # noqa: BLE001 — recorded per file, batch continues
            await registry.file_failed(job_id, index, str(exc))
        done += 1
        percent = _SPLIT_CEILING + int((100 - _SPLIT_CEILING) * done / total)
        await registry.set_status(job_id, percent=percent)

    job = await registry.get(job_id)
    landed = any(f.version_ids for f in job.files) if job else False
    await registry.set_status(
        job_id,
        status=JobStatus.DONE if landed else JobStatus.FAILED,
        phase="done" if landed else "failed",
        percent=100,
    )
