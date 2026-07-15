"""Loading brand→generic aliases from a registry.

The table (0007) and the expansion (#16) work. The table is empty, and that is not an
oversight — the data cannot be invented. Brand names are assigned per market by national
authorities, they change, and a wrong one answers confidently about the wrong drug. The
`source` column exists so every row is answerable to a human; filling it with a guess and
writing "probably" in that column would defeat the column.

So this is the path in, not the data. It does three things a bulk INSERT would not:

**It refuses to resolve a conflict.** If the table says a brand means one generic and the
input says it means another, one of them is wrong and this cannot know which. Taking the
newer row silently is a wrong-drug bug with no symptom. Conflicts are reported and not
applied.

**It requires a source per load.** Not per row for ergonomics, but never absent.

**It fails early on what the database would reject anyway.** The CHECK constraints catch
a malformed alias — but as an IntegrityError halfway through a 3,000-row file, with no
indication which row. This reports every bad row before writing any.
"""

from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alias import DrugAlias


@dataclass(frozen=True)
class AliasRow:
    alias: str
    generic_name: str


@dataclass(frozen=True)
class AliasConflict:
    """The same brand, two different generics. Reported, never resolved."""

    alias: str
    existing_generic: str
    existing_source: str
    incoming_generic: str


@dataclass(frozen=True)
class RejectedRow:
    alias: str
    reason: str


@dataclass
class LoadReport:
    inserted: int = 0
    unchanged: int = 0
    conflicts: list[AliasConflict] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)

    @property
    def needs_a_human(self) -> bool:
        """A conflict is a disagreement about what a drug is. Nothing here can settle it."""
        return bool(self.conflicts)


def _validate(row: AliasRow) -> str | None:
    alias = row.alias.strip()
    generic = row.generic_name.strip()

    if not alias:
        return "empty alias"
    if not generic:
        return "empty generic_name"
    if alias != alias.lower():
        # The DB CHECK enforces this too. Matching lowercases the query, so an uppercase
        # row would simply never fire — a silent no-op rather than an error.
        return "alias must be lowercase"
    if alias == generic.lower():
        return "alias is the same as the generic name"
    if len(alias) > 128 or len(generic) > 128:
        return "alias or generic_name exceeds 128 characters"
    if len(alias) < 3:
        # A two-character alias matches inside ordinary words often enough to be a
        # liability, even with word boundaries. If a real registry has one, it needs
        # deciding on rather than defaulting through.
        return "alias is shorter than 3 characters — too generic to match safely"
    return None


def read_csv(path: Path) -> tuple[list[AliasRow], list[RejectedRow]]:
    """Read `alias,generic_name` rows. Header required."""
    rows: list[AliasRow] = []
    rejected: list[RejectedRow] = []

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {"alias", "generic_name"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing column(s): {', '.join(sorted(missing))}")

        for line in reader:
            alias = (line.get("alias") or "").strip()
            generic = (line.get("generic_name") or "").strip()
            if not alias and not generic:
                continue  # blank line
            rows.append(AliasRow(alias=alias, generic_name=generic))

    return rows, rejected


async def load_aliases_from_rows(
    session: AsyncSession, rows: list[AliasRow], *, source: str
) -> LoadReport:
    """Insert new aliases. Caller commits.

    `source` is where these came from — a registry name and edition, a person, a URL.
    Nothing enforces that it is meaningful, but a row without one cannot be corrected by
    anyone who was not in the room.
    """
    if not source.strip():
        raise ValueError("source is required: an alias nobody can trace is one nobody can correct")

    report = LoadReport()

    valid: list[AliasRow] = []
    for row in rows:
        reason = _validate(row)
        if reason is None:
            valid.append(AliasRow(alias=row.alias.strip(), generic_name=row.generic_name.strip()))
        else:
            report.rejected.append(RejectedRow(alias=row.alias, reason=reason))

    # Two rows in the same file claiming different generics for one brand. Neither is
    # trustworthy, so *neither* is applied — an earlier draft kept the first and reported
    # only the second, which quietly makes file order the arbiter of what a drug is.
    claims: dict[str, list[str]] = {}
    for row in valid:
        claims.setdefault(row.alias, []).append(row.generic_name)

    contested: set[str] = set()
    for alias, generics in claims.items():
        distinct = sorted(set(generics))
        if len(distinct) > 1:
            contested.add(alias)
            report.conflicts.append(
                AliasConflict(
                    alias=alias,
                    existing_generic=distinct[0],
                    existing_source=f"{source} (another row in the same input)",
                    incoming_generic=distinct[1],
                )
            )

    deduped = [
        AliasRow(alias=alias, generic_name=generics[0])
        for alias, generics in claims.items()
        if alias not in contested
    ]

    if not deduped:
        return report

    existing = {
        row.alias: row
        for row in (
            await session.execute(
                select(DrugAlias).where(DrugAlias.alias.in_([r.alias for r in deduped]))
            )
        ).scalars()
    }

    to_insert: list[dict] = []
    for row in deduped:
        current = existing.get(row.alias)
        if current is None:
            to_insert.append(
                {
                    "id": uuid.uuid4(),
                    "alias": row.alias,
                    "generic_name": row.generic_name,
                    "source": source,
                }
            )
        elif current.generic_name == row.generic_name:
            report.unchanged += 1
        else:
            # The dangerous case. Overwriting would silently change what a brand means,
            # and the previous answer would already have been given.
            report.conflicts.append(
                AliasConflict(
                    alias=row.alias,
                    existing_generic=current.generic_name,
                    existing_source=current.source,
                    incoming_generic=row.generic_name,
                )
            )

    if to_insert:
        # ON CONFLICT DO NOTHING guards the race between the SELECT above and here; the
        # unique constraint is the guarantee, as ever.
        await session.execute(
            pg_insert(DrugAlias).values(to_insert).on_conflict_do_nothing(index_elements=["alias"])
        )
        report.inserted = len(to_insert)

    return report


async def load_aliases_from_csv(session: AsyncSession, path: Path, *, source: str) -> LoadReport:
    rows, rejected = read_csv(path)
    report = await load_aliases_from_rows(session, rows, source=source)
    report.rejected.extend(rejected)
    return report
