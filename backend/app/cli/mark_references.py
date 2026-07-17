"""Flag existing bibliography chunks so retrieval stops surfacing them (#50).

    python -m app.cli.mark_references            # report what WOULD change; changes nothing
    python -m app.cli.mark_references --apply     # set is_reference to match content

New documents are flagged at ingestion (services/indexing.py). This backfills the corpus that
was ingested before the flag existed, and re-runs safe: it sets is_reference to exactly what
looks_like_reference() says for the current content, so running it twice is a no-op.

**A report-first CLI, not a migration, for the same reason as repair_hashes: this decides
which chunks may answer a clinician, and that decision should be inspectable before it takes
effect and reversible after.** It prints per-document counts and a sample, and only writes on
--apply. `looks_like_reference` was measured to flag no guidance chunk (services/references.py),
and the definitive gate is the eval — a re-run after --apply must regress no answering question.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion
from app.services.references import looks_like_reference


async def _run(apply: bool) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            rows = (
                await session.execute(
                    select(
                        Chunk.id,
                        Chunk.content,
                        Chunk.is_reference,
                        Chunk.page_start,
                        Document.issuing_org,
                    )
                    .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
                    .join(Document, DocumentVersion.document_id == Document.id)
                )
            ).all()

            # Compute the target flag for every chunk; collect the ones that must change.
            changes: list[tuple] = []  # (id, should_be)
            per_org: dict[str, list[int]] = {}
            samples: list[tuple[str, int, str]] = []
            for cid, content, current, page, org in rows:
                should = looks_like_reference(content)
                bucket = per_org.setdefault(org, [0, 0])
                bucket[1] += 1
                if should:
                    bucket[0] += 1
                    if len(samples) < 8:
                        samples.append((org, page, content[:74].strip()))
                if should != current:
                    changes.append((cid, should))

            print(f"{'org':8}{'references':>12}{'total':>7}{'%':>7}")
            for org, (refs, total) in sorted(per_org.items()):
                print(f"{org:8}{refs:>12}{total:>7}{100 * refs / total:>6.1f}%")
            print(f"\n{len(changes)} chunk(s) would change is_reference.")
            print("sample of chunks flagged as references:")
            for org, page, text in samples:
                print(f"  [{org}] p{page}: {text!r}")

            if not apply:
                print("\nreport only — pass --apply to write. Then re-run the eval as the gate.")
                return 0
            if not changes:
                print("\nnothing to change; is_reference already matches content.")
                return 0

            to_true = [cid for cid, should in changes if should]
            to_false = [cid for cid, should in changes if not should]
            for ids, value in ((to_true, True), (to_false, False)):
                if ids:
                    await session.execute(
                        update(Chunk).where(Chunk.id.in_(ids)).values(is_reference=value)
                    )
            await session.commit()

            flagged = (
                await session.execute(
                    select(func.count()).select_from(Chunk).where(Chunk.is_reference.is_(True))
                )
            ).scalar_one()
            print(f"\napplied. {len(to_true)} set true, {len(to_false)} set false; "
                  f"{flagged} chunks now flagged as references.")
            return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="write the flags (default: report only)")
    return asyncio.run(_run(p.parse_args(argv).apply))


if __name__ == "__main__":
    sys.exit(main())
