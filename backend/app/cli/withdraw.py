"""Withdraw a guideline from search, or restore it — the licence firewall (#49).

    python -m app.cli.withdraw --list                          # what is indexed, and its status
    python -m app.cli.withdraw --org KDIGO --reason "#49 ..."   # withdraw every KDIGO version
    python -m app.cli.withdraw --org KDIGO --restore            # put it back (a granted licence)

Selection is by any of `--org`, `--id`, or `--title-contains`, unioned. `--org` alone is too
coarse when one issuing_org holds documents of different provenance: every German legal document
here — the official §5-UrhG law texts AND the pirated commercial commentaries — carries
issuing_org=`Bundesrecht`, so `--org Bundesrecht` cannot separate the 1,086 public-domain chunks
from the 10,222 commercial ones. `--id` (a document id, repeatable) and `--title-contains` (a
title substring, repeatable) select at document granularity. Verify what a document IS by its
source, never by its title: `hoai-2013-praxisleitfaden-simmendinger` reads like the official HOAI
by name and is a copyrighted Simmendinger book by source.

    # withdraw exactly the commercial editions, leaving the official law texts active:
    python -m app.cli.withdraw \
        --title-contains "Kommentar zur VOB/C" \
        --title-contains "hoai-2013-praxisleitfaden-simmendinger" \
        --title-contains "Die_neue_HOAI_2013" \
        --title-contains "voba-2016-textausgabe" \
        --title-contains "vob-b-nach-anspruchen" \
        --title-contains "ISO_310002018" \
        --title-contains "Schwindel (M. Strupp" \
        --reason "copyright: commercial/pirated editions, §19a UrhG" --dry-run

**Why this is a CLI and not a raw UPDATE.** Which documents a system may lawfully index is a
decision with a reason and a direction, and a `psql` one-liner records neither. This prints
what it will change before it changes it, names the reason, and is symmetric — the same tool
restores, so a granted licence is one command, not an archaeology dig through migration files.

**Why a status change and not a DELETE.** `withdrawn` leaves the chunks and the PDF in place
(see VersionStatus.WITHDRAWN). Retrieval already returns only `active` and, on request,
`archived`, so a withdrawn version cannot answer a query by any path — while remaining exactly
recoverable. Deleting would make "we obtained a licence" mean "re-ingest 163 pages"; withdrawing
makes it mean one flag. It also keeps the audit trail honest: a past query that cited a
now-withdrawn chunk still resolves, because the chunk still exists.

**It refuses to withdraw the last active copy of nothing.** With no selector match it changes
nothing and says so, rather than reporting success over an empty set — the same failure the
eval's `answered 15/18` taught us to distrust. `--dry-run` prints the same plan without
committing, so a bulk withdrawal can be inspected against prod before it lands.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.document import Document, DocumentVersion, VersionStatus


async def _inventory(session: AsyncSession) -> list[tuple[str, str, str, int]]:
    """(issuing_org, title, status, chunk_count) per version, so a withdrawal is decided
    against what is actually indexed rather than what is assumed to be."""
    from app.models.chunk import Chunk

    rows = (
        await session.execute(
            select(
                Document.issuing_org,
                Document.title,
                DocumentVersion.status,
                func.count(Chunk.id),
            )
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .outerjoin(Chunk, Chunk.document_version_id == DocumentVersion.id)
            .group_by(Document.issuing_org, Document.title, DocumentVersion.status)
        )
    ).all()
    return [
        (org, title, status.value if hasattr(status, "value") else str(status), n)
        for org, title, status, n in rows
    ]


def _selector(org, ids, title_contains):
    """The document-matching predicate, unioned across every selector given. Returns None when
    no selector was passed — the caller treats that as "inspect, do not change"."""
    conds = []
    if org:
        conds.append(Document.issuing_org == org)
    if ids:
        # Parse here rather than in argparse so a bad id is one clear error, not a stack trace.
        conds.append(Document.id.in_([uuid.UUID(str(i)) for i in ids]))
    for sub in title_contains:
        # autoescape so a literal '_' in a title (Die_neue_HOAI_2013, ISO_310002018) is matched
        # as an underscore and not as LIKE's single-character wildcard.
        conds.append(Document.title.contains(sub, autoescape=True))
    return or_(*conds) if conds else None


def _describe_selection(org, ids, title_contains) -> str:
    parts = []
    if org:
        parts.append(f"org={org}")
    if ids:
        parts.append(f"{len(ids)} id(s)")
    if title_contains:
        parts.append(f"{len(title_contains)} title pattern(s)")
    return ", ".join(parts) or "(no selector)"


async def run_withdrawal(
    session: AsyncSession,
    *,
    org: str | None = None,
    ids=(),
    title_contains=(),
    restore: bool = False,
    reason: str | None = None,
    dry_run: bool = False,
    do_list: bool = False,
) -> int:
    """The whole operation against a supplied session, so it is testable without a process.

    Returns a shell exit code: 0 changed-or-inspected-ok, 1 nothing-matched (the guard against
    reporting success over an empty set), 2 a required argument is missing. Commits on a real
    change; a `dry_run` commits nothing.
    """
    selector = _selector(org, ids, title_contains)
    if do_list or selector is None:
        inv = await _inventory(session)
        if not do_list:
            print("nothing to do: pass --org/--id/--title-contains to withdraw or "
                  "restore, or --list to inspect\n")
        print(f"{'issuing_org':10} {'status':10} {'chunks':>7}  title")
        for org_, title, status, n in sorted(inv):
            print(f"{org_:10} {status:10} {n:>7}  {title[:52]}")
        return 0 if do_list else 1

    target = VersionStatus.ACTIVE if restore else VersionStatus.WITHDRAWN
    verb = "restore" if restore else "withdraw"
    if not restore and not reason:
        print(f"error: --reason is required to {verb} (it is recorded in the log line)")
        return 2

    versions = (
        await session.execute(
            select(DocumentVersion, Document.title)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(selector)
        )
    ).all()
    described = _describe_selection(org, ids, title_contains)
    if not versions:
        print(f"no versions match [{described}]; nothing changed")
        return 1

    changed = 0
    for version, title in versions:
        if version.status == target:
            print(f"  already {target.value}: {title[:50]}")
            continue
        print(f"  {version.status.value if hasattr(version.status,'value') else version.status} "
              f"-> {target.value}: {title[:50]}")
        changed += 1

    if changed == 0:
        print(f"every matched version is already {target.value}; nothing changed")
        return 0

    log_reason = "restored to active" if restore else reason
    if dry_run:
        print(f"\n[dry-run] would {verb}: {described} — {changed} version(s) — "
              f"{log_reason}\n(nothing committed)")
        return 0

    # Apply only now that the plan has been printed — a --dry-run and a real run show the
    # identical plan, so what was previewed is exactly what commits.
    for version, _title in versions:
        version.status = target
    print(f"\n{verb}: {described} — {changed} version(s) — {log_reason}")
    await session.commit()
    return 0


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            return await run_withdrawal(
                session,
                org=args.org,
                ids=args.ids,
                title_contains=args.title_contains,
                restore=args.restore,
                reason=args.reason,
                dry_run=args.dry_run,
                do_list=args.list,
            )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", help="issuing_org to withdraw or restore (e.g. KDIGO)")
    p.add_argument("--id", action="append", default=[], dest="ids",
                   help="document id to select (repeatable)")
    p.add_argument("--title-contains", action="append", default=[], dest="title_contains",
                   help="select documents whose title contains this substring (repeatable)")
    p.add_argument("--reason", help="why (required to withdraw; recorded in the log line)")
    p.add_argument("--restore", action="store_true", help="set status back to active")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan and change nothing (inspect a bulk withdrawal first)")
    p.add_argument("--list", action="store_true", help="show every version and its status")
    return asyncio.run(_run(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
