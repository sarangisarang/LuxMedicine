"""Verify the audit chain and record a checkpoint. For cron.

    python -m app.cli.verify_chain --verified-by "nightly cron"

Exits 0 when intact, 1 when broken. That exit code is the whole interface: it is what
lets any scheduler alert without knowing anything about this system.

Copy the printed checkpoint line somewhere this system cannot reach — a WORM bucket, an
append-only log, a printout. A checkpoint stored only in the database it vouches for is
worth what that database is worth, and the point of a checkpoint is to bound a break by
something the attacker did not control.
"""

import argparse
import asyncio
import sys

from app.db.session import SessionLocal
from app.services.chain_monitor import export_checkpoint, verify_and_checkpoint


async def _run(verified_by: str) -> int:
    async with SessionLocal() as session:
        result = await verify_and_checkpoint(session, verified_by=verified_by)
        line = export_checkpoint(result)

        if not result.intact:
            print(line, file=sys.stderr)
            if not result.break_is_bounded:
                print(
                    "No prior checkpoint exists, so the break cannot be placed in time. "
                    "Every row since genesis is in question.",
                    file=sys.stderr,
                )
            return 1

        print(line)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verified-by",
        default="cli",
        help="Who or what ran this. Recorded on the checkpoint; a checkpoint nobody can "
        "attribute is one nobody can question.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.verified_by))


if __name__ == "__main__":
    raise SystemExit(main())
