"""Check — and only where it is provably safe, repair — `document_versions.file_hash` (#46).

    python -m app.cli.repair_hashes            # report only; changes nothing
    python -m app.cli.repair_hashes --repair   # correct the rows that are provably placeholders

**Why this is a CLI and not a migration.** `file_hash` is an evidential field: `audit_export`
reports it so a clinic can show *which bytes* a citation came from. A migration that quietly
rewrites one value with another is the shape of the problem, not the fix — the point of the
column is that nobody can change what a version is recorded as containing without saying so.

**The distinction this tool exists to make.** Two rows can both mismatch and mean opposite
things:

- *A placeholder.* `uuid4().hex` zero-padded to sha256's width, written by whatever seeded
  the corpus before `app/cli/ingest.py` existed, because the column is NOT NULL and something
  had to go in it. It was never a claim about the bytes, so correcting it makes the record
  true for the first time. Provable: the first 32 characters parse as a version-4 UUID and
  the remaining 32 are zeros. Nothing that hashes anything produces that.
- *A real sha256 that no longer matches.* That is a file that changed after it was
  registered, and it is an incident. This tool reports it and **refuses to touch it** —
  "repairing" it would erase the only evidence that it happened.

Anything that is neither is reported and left alone. When in doubt this does nothing, which
is the only safe default for a column whose job is to be trusted.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.document import DocumentVersion
from app.services import storage

_READ_CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def is_placeholder(value: str) -> bool:
    """Whether a stored file_hash is provably not a hash of anything.

    The shape the seed script left: `uuid4().hex` (32 hex) padded with 32 zeros to fill a
    64-character field. A version-4 UUID carries its version and variant markers in fixed
    positions, so this is not a guess — no digest of any input produces 32 zeros in its
    second half next to a well-formed v4 UUID in its first.
    """
    if len(value) != 64 or value[32:] != "0" * 32:
        return False
    try:
        return uuid.UUID(value[:32]).version == 4
    except ValueError:
        return False


@dataclass
class Finding:
    version_id: uuid.UUID
    label: str
    stored: str
    actual: str | None
    uri: str

    @property
    def state(self) -> str:
        if self.actual is None:
            return "file_missing"
        if self.actual == self.stored:
            return "ok"
        if is_placeholder(self.stored):
            return "placeholder"
        return "CONTENT_CHANGED"


async def inspect(session: AsyncSession, root: Path) -> list[Finding]:
    versions = (await session.execute(select(DocumentVersion))).scalars().all()
    findings = []
    for v in versions:
        path = storage.resolve(v.storage_uri, root=root)
        actual = sha256_of(path) if path.is_file() else None
        findings.append(
            Finding(
                version_id=v.id, label=v.version_label, stored=v.file_hash,
                actual=actual, uri=v.storage_uri,
            )
        )
    return findings


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            findings = await inspect(session, settings.storage_root)

            for f in findings:
                print(f"{f.state:16} {f.label:8} {f.uri}")
                if f.state != "ok":
                    print(f"                 stored: {f.stored}")
                    print(f"                 actual: {f.actual}")

            changed = [f for f in findings if f.state == "CONTENT_CHANGED"]
            placeholders = [f for f in findings if f.state == "placeholder"]

            if changed:
                # Loud, and never repaired: a real sha256 that stopped matching is a file
                # that changed after it was registered. Overwriting the hash would delete
                # the only record that it did.
                print(
                    f"\n!! {len(changed)} version(s) carry a REAL hash that no longer matches "
                    "their bytes.\n"
                    "!! That is a file changed after registration, not a recording bug, and "
                    "this tool will not touch it.\n"
                    "!! Investigate before doing anything else."
                )

            if not placeholders:
                print("\nnothing to repair.")
                return 1 if changed else 0

            print(f"\n{len(placeholders)} version(s) have a placeholder where a hash belongs:")
            for f in placeholders:
                print(f"  {f.label}: {f.stored[:32]}…(uuid+zeros) -> {f.actual}")

            if not args.repair:
                print("\nreport only. Re-run with --repair to correct these.")
                return 0

            for f in placeholders:
                v = await session.get(DocumentVersion, f.version_id)
                v.file_hash = f.actual
                print(f"repaired {f.label}: file_hash now the sha256 of its bytes")
            await session.commit()
            print(f"\n{len(placeholders)} row(s) corrected.")
            return 1 if changed else 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--repair",
        action="store_true",
        help="correct rows whose stored value is provably a placeholder; never touches a "
        "real hash that stopped matching",
    )
    return asyncio.run(main_async(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
