"""Add self-describing table rows to an already-indexed document, without deleting anything (#48).

    python -m app.cli.augment_tables --org CDC            # report what would be added
    python -m app.cli.augment_tables --org CDC --apply     # add them

The self-describing rows of #48 ("ii. With aura — Cu-IUD: 1, ..., CHC: 4*") reach a clinician
only once they are in the index. A full re-index cannot do it here: the existing chunks are
cited by the audit trail and must not be deleted (the row has to survive for the trail to
resolve), and the same PDF cannot be registered as a new version (file_hash is unique). Both
constraints are correct — so this ADDS the self-describing rows as new chunks on the existing
version, alongside the originals, and deletes nothing.

The original headerless-row chunks stay searchable; the guard still refuses them. The new
self-describing chunks are what answer the question — they retrieve on it and quote cleanly with
each category carrying its method. A little content is duplicated (the table row, twice), which
is the price of never touching a cited chunk.

**A row this replaces is superseded in the same transaction as the insert.** Insert-only is
right for the audit trail and wrong for the index: without this step, every fix to the
extractor leaves a duplicate layer behind, and the flawed row keeps answering next to its
replacement. That is not hypothetical — completing truncated labels produced 88 such pairs, and
"ii. Systolic ≥160 mm Hg or" went on out-ranking the full threshold until they were retired by
hand. Doing it here makes that the last time: the invariant is one active chunk per printed
row. Marked, never deleted, so no exception is needed for the cited rows a delete could not
touch anyway. Pairing is `services/row_supersession.replaces`, and it refuses rather than
guesses.

Idempotent: a row already present as a chunk is skipped and an already-superseded row is not
reconsidered, so a second run adds and retires nothing. Local embedder, no quota.
Report-first; only --apply writes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

import pdfplumber
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion
from app.services import storage
from app.services.embedding import make_embedder
from app.services.references import looks_like_reference
from app.services.row_supersession import replaces
from app.services.honorar_tables import self_describing_honorar_rows
from app.services.table_extraction import self_describing_lines


def _rows_for_version(pdf_path) -> list[tuple[int, str]]:
    """(page_number, self_describing_line) for every mappable table row in the PDF.

    Both families, the same two the ingest path (services/extraction.extract_pdf) emits, so an
    already-indexed document gains exactly what a fresh ingest would: US MEC category rows and
    HOAI Honorartafel fee rows. The fee pass is gated on the page naming a Honorarzone, because
    extract_tables is not free and most pages have no table.
    """
    out: list[tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            for line in self_describing_lines(page.extract_words()):
                out.append((index, line))
            if "Honorarzone" in (page.extract_text() or ""):
                for line in self_describing_honorar_rows(page.extract_tables()):
                    out.append((index, line))
            page.flush_cache()
    return out


async def _supersede_replaced(session: AsyncSession, version_id, payload: list[dict]) -> int:
    """Point every row these new ones replace at its replacement. Caller commits.

    Pairing is `row_supersession.replaces` plus same-page and same-version, and nothing else —
    no proximity, no similarity score. The measurement that first found these pairs had a bug
    on this exact path (it compared raw labels and missed that a completed row loses its
    enumerator too, reporting 60 of 88), so the rule is pinned by tests rather than trusted,
    and it refuses rather than guesses: a different category vector, an equal or shorter
    label, or an already-complete original all mean "not a replacement".

    Only rows that are not already superseded are considered, so a second run is a no-op.
    """
    by_page: dict[int, list[dict]] = {}
    for row in payload:
        by_page.setdefault(row["page_start"], []).append(row)

    candidates = (
        await session.execute(
            select(Chunk.id, Chunk.page_start, Chunk.content).where(
                Chunk.document_version_id == version_id,
                Chunk.superseded_by.is_(None),
                Chunk.page_start.in_(list(by_page)),
            )
        )
    ).all()

    retired = 0
    for chunk_id, page, content in candidates:
        for row in by_page.get(page, ()):
            if row["id"] == chunk_id:
                continue
            if replaces(content, row["content"]):
                await session.execute(
                    update(Chunk).where(Chunk.id == chunk_id).values(superseded_by=row["id"])
                )
                retired += 1
                break
    return retired


async def _run(org: str, apply: bool, title_contains: str | None) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # make_embedder(), not E5Embedder(): on the deployment the corpus is embedded with the
    # int8 ONNX model, and augmenting with the fp32 sentence-transformers model would write
    # rows whose vectors come from different weights than the queries — a silent retrieval
    # penalty on exactly the rows this exists to make findable. Same bug the ingest CLI had.
    embedder = None
    try:
        async with maker() as session:
            query = (
                select(DocumentVersion.id, DocumentVersion.storage_uri, Document.title)
                .join(Document, DocumentVersion.document_id == Document.id)
                .where(Document.issuing_org == org)
            )
            # An org can hold several documents; --title-contains narrows to specific ones. It
            # is how a fee-table augment is aimed at the official HOAI alone, leaving every other
            # document under the same issuing_org untouched.
            if title_contains:
                query = query.where(Document.title.ilike(f"%{title_contains}%"))
            versions = (await session.execute(query)).all()
            if not versions:
                where = f"issuing_org={org!r}"
                if title_contains:
                    where += f" and title contains {title_contains!r}"
                print(f"no versions with {where}; nothing to do")
                return 1

            total_new = 0
            for vid, storage_uri, title in versions:
                rows = _rows_for_version(storage.resolve(storage_uri, root=settings.storage_root))
                existing = {
                    c for (c,) in (
                        await session.execute(
                            select(Chunk.content).where(Chunk.document_version_id == vid)
                        )
                    ).all()
                }
                fresh = [(pg, line) for pg, line in rows if line not in existing]
                print(f"  {title[:46]:46} {len(rows):>4} mappable rows, {len(fresh):>4} new")
                total_new += len(fresh)

                if not apply or not fresh:
                    continue

                if embedder is None:
                    embedder = make_embedder()
                base = (
                    await session.execute(
                        select(func.coalesce(func.max(Chunk.ordinal), 0)).where(
                            Chunk.document_version_id == vid
                        )
                    )
                ).scalar_one()
                vectors = embedder.embed_passages([line for _, line in fresh])
                payload = [
                    {
                        "id": uuid.uuid4(),
                        "document_version_id": vid,
                        # After the originals, so ordinals stay unique and monotonic.
                        "ordinal": base + 1 + i,
                        "page_start": pg,
                        "page_end": pg,
                        "section": None,
                        "content": line,
                        "embedding": vector,
                        # A category table row is guidance, never a reference.
                        "is_reference": looks_like_reference(line),
                    }
                    for i, ((pg, line), vector) in enumerate(zip(fresh, vectors, strict=True))
                ]
                await session.execute(insert(Chunk), payload)

                # Retire what these rows replace, in the SAME transaction as the insert.
                #
                # Without this, every extractor fix leaves a duplicate layer behind: the
                # corrected row is added and the flawed one keeps answering beside it, so a
                # truncated label competes with its completed twin and somebody has to notice
                # and clean it up by hand afterwards. That happened once (88 rows) and the
                # cleanup is the thing being removed here, not repeated.
                #
                # Marked, never deleted: 0008 forbids removing a cited chunk, and an
                # exception for cited rows would leave exactly the ones with history still
                # live in the index. Superseding needs no exception.
                retired = await _supersede_replaced(session, vid, payload)
                await session.commit()
                print(f"    added {len(payload)} self-describing chunks to {title[:40]}")
                if retired:
                    print(f"    superseded {retired} row(s) these replace")

            if not apply:
                print(f"\nreport only — {total_new} rows would be added. Pass --apply, then run the eval.")
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", required=True, help="issuing_org to augment (e.g. CDC)")
    p.add_argument("--apply", action="store_true", help="write the new chunks (default: report only)")
    p.add_argument(
        "--title-contains",
        default=None,
        help="only augment documents whose title contains this (e.g. 'HOAI — Honorarordnung' "
        "to hit the official statute and not other documents under the same org)",
    )
    args = p.parse_args(argv)
    return asyncio.run(_run(args.org, args.apply, args.title_contains))


if __name__ == "__main__":
    sys.exit(main())
