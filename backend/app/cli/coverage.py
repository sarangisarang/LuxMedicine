"""Which pages of a document are actually in the index — and which are not.

    python -m app.cli.coverage                 # every document
    python -m app.cli.coverage --org NHLBI     # one issuing organisation

**Why this exists, and it is not bookkeeping.** NHLBI EPR-3 has 189 pages whose chunks were
refused at indexing (#41: glyphs the font never named). That is recorded and it is "loud" in
the sense that `unreadable_pages` holds it — but the content is still absent, and nothing puts
it in front of anyone writing questions.

The trap that creates: expand the eval by reading the PDF and writing questions from what it
says, and some of those questions land on pages the index does not contain. The system will
correctly answer "no guidance found", the harness will score `wrongly_declined`, and the run
will read as an accuracy problem. It is a **coverage** problem, and the two are not fixable by
the same work. A number that mixes them measures neither.

So before writing a question from page N, this says whether page N is answerable at all.

**Two numbers, and neither is the other.** `unreadable` is what indexing recorded as damaged;
`NOT ANSWERABLE` is what has no live chunk now. On NHLBI they are 189 and 116 — the 73-page
difference is pages where SOME chunks were refused and others survived. That makes NOT
ANSWERABLE the **floor** of the hole, not its size: a page that kept two of its five chunks
counts as present here and can still be missing the exact passage a question was written from.
Treat a partially-damaged page as suspect, not as covered.

**It carries its own positive control.** A detector's zero is only worth believing once it has
been seen to fire on a case known to be damaged (today's lesson, learned three times). NHLBI is
that case: `--verify` asserts this reports NHLBI's missing pages, so a run that finds nothing
anywhere has been shown to be capable of finding something.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


def _ranges(pages: list[int]) -> str:
    """Compact a page list: [1,2,3,7,9,10] -> '1-3, 7, 9-10'."""
    if not pages:
        return "-"
    out: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        out.append(f"{start}-{previous}" if start != previous else f"{start}")
        start = previous = page
    out.append(f"{start}-{previous}" if start != previous else f"{start}")
    return ", ".join(out)


async def coverage(session: AsyncSession, org: str | None) -> list[dict]:
    """Per version: which pages hold an active chunk, and which hold nothing."""
    clause = "WHERE d.issuing_org = :org" if org else ""
    versions = (
        await session.execute(
            text(
                "SELECT v.id, d.title, d.issuing_org, d.sector, v.version_label, "
                "       v.unreadable_pages "
                f"FROM document_versions v JOIN documents d ON v.document_id = d.id {clause} "
                "ORDER BY d.sector, d.issuing_org, d.title"
            ),
            {"org": org} if org else {},
        )
    ).all()

    report: list[dict] = []
    for vid, title, issuing_org, sector, label, unreadable in versions:
        rows = (
            await session.execute(
                text(
                    "SELECT min(page_start) AS lo, max(page_end) AS hi FROM chunks "
                    "WHERE document_version_id = :v"
                ),
                {"v": vid},
            )
        ).one()
        if rows.lo is None:
            report.append(
                {
                    "title": title, "org": issuing_org, "sector": sector, "label": label,
                    "span": None, "indexed": 0, "missing": [], "unreadable": list(unreadable or []),
                    "note": "no chunks at all — pending, or indexing produced nothing",
                }
            )
            continue

        live = {
            page
            for (page,) in (
                await session.execute(
                    text(
                        "SELECT DISTINCT page_start FROM chunks "
                        "WHERE document_version_id = :v AND superseded_by IS NULL"
                    ),
                    {"v": vid},
                )
            ).all()
        }
        missing = [p for p in range(rows.lo, rows.hi + 1) if p not in live]
        report.append(
            {
                "title": title, "org": issuing_org, "sector": sector, "label": label,
                "span": (rows.lo, rows.hi), "indexed": len(live), "missing": missing,
                # NULL means nobody measured — a different claim from "measured and clean".
                "unreadable": None if unreadable is None else list(unreadable),
                "note": None,
            }
        )
    return report


async def main(org: str | None, verify: bool) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession)
    try:
        async with maker() as session:
            rows = await coverage(session, org)

        print("\npage coverage — a question must not be written from a page listed as missing\n")
        for row in rows:
            span = f"p{row['span'][0]}-{row['span'][1]}" if row["span"] else "(none)"
            print(f"{row['sector']:8} {row['org']:12} {row['title'][:44]:44} {span}")
            print(f"{'':21} indexed pages : {row['indexed']}")
            if row["note"]:
                print(f"{'':21} {row['note']}")
            unreadable = row["unreadable"]
            if unreadable is None:
                print(f"{'':21} unreadable    : NULL — nobody measured, not 'clean'")
            else:
                print(f"{'':21} unreadable    : {len(unreadable)} page(s)")
            missing = row["missing"]
            print(f"{'':21} NOT ANSWERABLE: {len(missing)} page(s)  {_ranges(missing)[:90]}")
            print()

        if verify:
            # The positive control. NHLBI is known-damaged (189 refused pages); a run of this
            # tool that cannot see that has not earned belief in any of its zeroes.
            nhlbi = [r for r in rows if r["org"] == "NHLBI"]
            if not nhlbi:
                print("VERIFY: no NHLBI version present — the positive control cannot run")
                return 2
            found = sum(len(r["missing"]) for r in nhlbi)
            print(f"VERIFY: NHLBI reports {found} unanswerable page(s)")
            if found == 0:
                print("VERIFY FAILED — the known-damaged document reports none, so this")
                print("detector is blind and its zeroes elsewhere mean nothing.")
                return 1
            print("VERIFY OK — the detector fires on the case known to be damaged.")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default=None, help="limit to one issuing_org")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="assert the detector fires on NHLBI, the document known to have refused pages",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.org, args.verify)))
