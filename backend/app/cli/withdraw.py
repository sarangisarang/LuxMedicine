"""Withdraw a guideline from search, or restore it — the licence firewall (#49).

    python -m app.cli.withdraw --list                          # what is indexed, and its status
    python -m app.cli.withdraw --org KDIGO --reason "#49 ..."   # withdraw every KDIGO version
    python -m app.cli.withdraw --org KDIGO --restore            # put it back (a granted licence)

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

**It refuses to withdraw the last active copy of nothing.** With no `--org` match it changes
nothing and says so, rather than reporting success over an empty set — the same failure the
eval's `answered 15/18` taught us to distrust.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select
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


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            if args.list or not args.org:
                inv = await _inventory(session)
                if not args.list:
                    print("nothing to do: pass --org to withdraw/restore, or --list to inspect\n")
                print(f"{'issuing_org':10} {'status':10} {'chunks':>7}  title")
                for org, title, status, n in sorted(inv):
                    print(f"{org:10} {status:10} {n:>7}  {title[:52]}")
                return 0 if args.list else 1

            target = VersionStatus.ACTIVE if args.restore else VersionStatus.WITHDRAWN
            verb = "restore" if args.restore else "withdraw"
            if not args.restore and not args.reason:
                print(f"error: --reason is required to {verb} (it is recorded in the log line)")
                return 2

            versions = (
                await session.execute(
                    select(DocumentVersion, Document.title)
                    .join(Document, DocumentVersion.document_id == Document.id)
                    .where(Document.issuing_org == args.org)
                )
            ).all()
            if not versions:
                print(f"no versions with issuing_org={args.org!r}; nothing changed")
                return 1

            changed = 0
            for version, title in versions:
                if version.status == target:
                    print(f"  already {target.value}: {title[:50]}")
                    continue
                print(f"  {version.status.value if hasattr(version.status,'value') else version.status} "
                      f"-> {target.value}: {title[:50]}")
                version.status = target
                changed += 1

            if changed == 0:
                print(f"every {args.org} version is already {target.value}; nothing changed")
                return 0

            reason = "restored to active" if args.restore else args.reason
            print(f"\n{verb}: {args.org} — {changed} version(s) — {reason}")
            await session.commit()
            return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", help="issuing_org to withdraw or restore (e.g. KDIGO)")
    p.add_argument("--reason", help="why (required to withdraw; recorded in the log line)")
    p.add_argument("--restore", action="store_true", help="set status back to active")
    p.add_argument("--list", action="store_true", help="show every version and its status")
    return asyncio.run(_run(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
