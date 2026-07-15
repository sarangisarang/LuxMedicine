"""Load brand-to-generic aliases from a registry CSV.

    python -m app.cli.load_aliases aliases.csv --source "Georgian State Drug Registry, 2026-06"

The CSV needs an `alias,generic_name` header. Exits non-zero on conflicts: a brand that
means two different drugs is a question for a pharmacist, not a merge strategy.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from app.db.session import SessionLocal
from app.services.alias_loader import load_aliases_from_csv


async def _run(path: Path, source: str, *, dry_run: bool) -> int:
    async with SessionLocal() as session:
        report = await load_aliases_from_csv(session, path, source=source)

        if report.rejected:
            print(f"rejected {len(report.rejected)} row(s):", file=sys.stderr)
            for row in report.rejected[:20]:
                print(f"  {row.alias!r}: {row.reason}", file=sys.stderr)
            if len(report.rejected) > 20:
                print(f"  ... and {len(report.rejected) - 20} more", file=sys.stderr)

        if report.conflicts:
            print(f"\n{len(report.conflicts)} CONFLICT(S) — not applied:", file=sys.stderr)
            for c in report.conflicts:
                print(
                    f"  {c.alias!r}: already {c.existing_generic!r} "
                    f"(from {c.existing_source!r}), input says {c.incoming_generic!r}",
                    file=sys.stderr,
                )
            print(
                "\nA brand meaning two drugs is a question for a pharmacist. Nothing was "
                "written for these; resolve them and re-run.",
                file=sys.stderr,
            )

        if dry_run:
            await session.rollback()
            print(f"\ndry run — would insert {report.inserted}, unchanged {report.unchanged}")
        else:
            await session.commit()
            print(f"\ninserted {report.inserted}, unchanged {report.unchanged}")

        return 1 if report.needs_a_human else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="CSV with an alias,generic_name header")
    parser.add_argument(
        "--source",
        required=True,
        help="Where these came from: registry name and edition, a URL, a person. "
        "Recorded on every row. An alias nobody can trace is one nobody can correct.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report and roll back")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"no such file: {args.csv}", file=sys.stderr)
        return 2

    return asyncio.run(_run(args.csv, args.source, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
