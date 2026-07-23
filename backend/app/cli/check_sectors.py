"""Check — and restore — the rule that a document's sector is derived, never chosen (#52).

    python -m app.cli.check_sectors            # report drift; exit 1 if any
    python -m app.cli.check_sectors --apply    # re-derive the drifted rows from their org

`Document.sector` says of itself: "Written by ingestion from the issuing organisation
(`IssuingOrg.sector`), never chosen separately, so the two cannot drift apart." Ingestion
honours that. Nothing else did — the column is a plain `String(32)` and one `UPDATE` is all it
takes to put a document in a corpus its issuer does not belong to. That is not a hypothetical:
on 2026-07-23 a Bundesrecht (legal) document was sitting in the medical corpus, and it was
answering clinical questions.

**Why the sector wall stays quiet when this happens.** Retrieval filters on `documents.sector`
and does so correctly — the mislabelled document is returned because the label says to return
it. There is no error, no warning, and nothing in the answer to hint that the source came from
the wrong corpus. An invisible filter is the risk we already named; an invisible *hole* in it
is worse, because the wall still reports itself as intact. The only way to see it is to compare
the stored value against the value the design says it must have, which is what this does.

**Exit 1 on drift, so this is usable as a guard**, not just a report — the same shape as
`coverage --verify`. A checker that reports a problem and exits 0 gets run once.

**`--apply` re-derives; it never takes a target sector as an argument.** Passing the intended
value would make this a second way to *choose* a sector — the very thing whose existence is
the bug. The org is the single source, so the fix can only ever be "recompute from the org".
If a document genuinely belongs to another corpus, that is a statement about its issuer, and
`IssuingOrg`/`_SECTORS` in `app.core.vocabulary` is where it has to be said.

Nothing is deleted and no chunk moves: this rewrites one column. A past query that cited the
document still resolves, and the document remains fully searchable — in its own corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.vocabulary import IssuingOrg
from app.models.document import Document


async def find_drift(session: AsyncSession) -> list[tuple[Document, str]]:
    """Every document whose stored sector differs from the one its org implies.

    Returns (document, expected_sector) pairs. An org that is not in the enum at all is a
    different failure — reported by the caller, not silently skipped, because a document
    nothing can classify is exactly as unreachable as a mislabelled one.
    """
    documents = (await session.execute(select(Document).order_by(Document.title))).scalars().all()
    drift: list[tuple[Document, str]] = []
    for document in documents:
        try:
            expected = IssuingOrg(document.issuing_org).sector.value
        except ValueError:
            drift.append((document, "?unknown-org?"))
            continue
        if expected != document.sector:
            drift.append((document, expected))
    return drift


async def _run(apply: bool) -> int:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as session:
            return await _check(session, apply)
    finally:
        await engine.dispose()


async def _check(session: AsyncSession, apply: bool) -> int:
    drift = await find_drift(session)
    total = len((await session.execute(select(Document.id))).scalars().all())

    if not drift:
        print(f"sectors ok: {total} documents, every one derived from its issuing org")
        return 0

    verb = "correcting" if apply else "DRIFT"
    for document, expected in drift:
        print(
            f"{verb}: {document.title[:60]} "
            f"(org={document.issuing_org}) sector={document.sector} -> {expected}"
        )
        if apply:
            if expected.startswith("?"):
                print("  skipped: its issuing org is not in IssuingOrg — fix the org first")
                continue
            document.sector = expected

    if not apply:
        print(f"\n{len(drift)} of {total} documents drifted. Re-derive them with --apply")
        return 1

    await session.commit()
    print(f"\ncorrected {len(drift)} of {total} documents")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="re-derive drifted sectors from the issuing org (default: report only)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
