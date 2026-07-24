"""The folder-upload's progress model — phases, percent, and one bad file not sinking the batch.

The per-part work is a fake here, so what is under test is purely the orchestration a 0-100% bar
depends on: that the job goes queued -> running -> done, that percent only climbs and ends at 100,
that a split document reports the right number of parts, and that a file that fails is recorded
without taking the others down with it.
"""

from __future__ import annotations

import uuid
from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.core.vocabulary import Sector
from app.services.ingest_jobs import JobRegistry, JobStatus, run_batch


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FakeIngest:
    """Records the titles it was asked to ingest; fails for any title in `fail_titles`."""

    def __init__(self, fail_titles: set[str] | None = None) -> None:
        self.titles: list[str] = []
        self._fail = fail_titles or set()

    async def __call__(self, title: str, part) -> uuid.UUID:
        self.titles.append(title)
        if title in self._fail:
            raise RuntimeError(f"index failed: {title}")
        return uuid.uuid4()


@pytest.mark.asyncio
async def test_a_small_file_runs_to_done_at_100_percent():
    reg = JobRegistry()
    job = await reg.create(sector=Sector.MEDICAL, filenames=["asthma.pdf"])
    ingest = FakeIngest()

    await run_batch(reg, job.id, [("asthma.pdf", _pdf(3))], ingest_part=ingest)

    final = await reg.get(job.id)
    assert final.status == JobStatus.DONE
    assert final.phase == "done" and final.percent == 100
    assert ingest.titles == ["asthma"]  # stem, no part suffix when unsplit
    assert final.files[0].parts == 1 and final.files[0].done_parts == 1
    assert len(final.files[0].version_ids) == 1


@pytest.mark.asyncio
async def test_a_large_file_is_ingested_as_numbered_parts():
    reg = JobRegistry()
    job = await reg.create(sector=Sector.LEGAL, filenames=["kommentar.pdf"])
    ingest = FakeIngest()

    await run_batch(reg, job.id, [("kommentar.pdf", _pdf(5))], ingest_part=ingest, max_pages=2)

    assert ingest.titles == ["kommentar (part 01)", "kommentar (part 02)", "kommentar (part 03)"]
    final = await reg.get(job.id)
    assert final.status == JobStatus.DONE
    assert final.files[0].parts == 3 and final.files[0].done_parts == 3


@pytest.mark.asyncio
async def test_percent_only_climbs_and_ends_at_100():
    reg = JobRegistry()
    job = await reg.create(sector=Sector.MEDICAL, filenames=["a.pdf", "b.pdf"])
    seen: list[int] = []

    async def watching_ingest(title, part):
        snap = await reg.get(job.id)
        seen.append(snap.percent)
        return uuid.uuid4()

    await run_batch(
        reg, job.id, [("a.pdf", _pdf(4)), ("b.pdf", _pdf(4))], ingest_part=watching_ingest, max_pages=2
    )

    final = await reg.get(job.id)
    assert final.percent == 100
    assert seen == sorted(seen), "percent must be monotonic during indexing"
    assert seen[0] >= 10, "indexing starts after the split budget"


@pytest.mark.asyncio
async def test_one_failing_file_does_not_sink_the_others():
    reg = JobRegistry()
    job = await reg.create(sector=Sector.MEDICAL, filenames=["good.pdf", "bad.pdf"])
    ingest = FakeIngest(fail_titles={"bad"})

    await run_batch(
        reg, job.id, [("good.pdf", _pdf(2)), ("bad.pdf", _pdf(2))], ingest_part=ingest
    )

    final = await reg.get(job.id)
    assert final.status == JobStatus.DONE, "something landed, so the batch is done, not failed"
    good = next(f for f in final.files if f.filename == "good.pdf")
    bad = next(f for f in final.files if f.filename == "bad.pdf")
    assert good.version_ids and good.error is None
    assert bad.error is not None and not bad.version_ids


@pytest.mark.asyncio
async def test_all_files_failing_marks_the_batch_failed():
    reg = JobRegistry()
    job = await reg.create(sector=Sector.MEDICAL, filenames=["x.pdf"])
    ingest = FakeIngest(fail_titles={"x"})

    await run_batch(reg, job.id, [("x.pdf", _pdf(2))], ingest_part=ingest)

    final = await reg.get(job.id)
    assert final.status == JobStatus.FAILED and final.percent == 100


@pytest.mark.asyncio
async def test_an_unreadable_upload_is_recorded_not_raised():
    reg = JobRegistry()
    job = await reg.create(sector=Sector.MEDICAL, filenames=["broken.pdf"])
    ingest = FakeIngest()

    await run_batch(reg, job.id, [("broken.pdf", b"not a pdf at all")], ingest_part=ingest)

    final = await reg.get(job.id)
    assert final.status == JobStatus.FAILED
    assert "could not read PDF" in final.files[0].error
    assert ingest.titles == [], "a file that could not be split is never handed to ingest"


@pytest.mark.asyncio
async def test_get_returns_a_copy_not_the_live_job():
    reg = JobRegistry()
    job = await reg.create(sector=Sector.MEDICAL, filenames=["a.pdf"])
    snapshot = await reg.get(job.id)
    snapshot.percent = 999  # mutating the snapshot must not touch the registry's job
    again = await reg.get(job.id)
    assert again.percent == 0
