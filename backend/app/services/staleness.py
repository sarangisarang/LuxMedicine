"""Which edition should the clinician actually be reading (#17).

`superseded_by` points at the *immediate* successor, which is not what a clinician needs
to be told. Given 2021 -> 2022 -> 2023, answering "a 2022 edition exists" sends them to
read another outdated document and call it done. The chain has to be walked to its tail,
so the warning names the edition that is current.

The tail is well-defined from any starting version: each node has at most one successor,
so the walk from a given edition is deterministic. That is *not* true of asking a
document for "its latest version" — a focused update that amends without replacing
leaves two unsuperseded editions, and neither is wrong. Walk forward from where the
clinician is, rather than guessing globally.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The recursion trusts an invariant enforced in supersession.py: cycles are impossible,
# because an edition may only be superseded by one that has no successor of its own.
# This cap exists for the day something writes superseded_by directly and bypasses that
# — a bounded wrong answer beats a query that never returns. If it ever trips, the
# supersession graph is broken and #12's guarantee has been circumvented.
MAX_CHAIN_DEPTH = 50

_CHAIN_SQL = """
WITH RECURSIVE chain AS (
    SELECT
        v.id AS start_id,
        v.id AS current_id,
        v.superseded_by,
        v.version_label,
        0 AS depth
    FROM document_versions v
    WHERE v.id = ANY(CAST(:version_ids AS uuid[]))

    UNION ALL

    SELECT
        c.start_id,
        v.id,
        v.superseded_by,
        v.version_label,
        c.depth + 1
    FROM chain c
    JOIN document_versions v ON v.id = c.superseded_by
    WHERE c.depth < :max_depth
)
SELECT start_id, version_label AS latest_label, depth
FROM chain
WHERE superseded_by IS NULL
"""


async def latest_labels(
    session: AsyncSession, version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """For each version, the label of the edition at the end of its chain.

    A version that is not superseded maps to its own label — the caller pairs this with
    `is_superseded` to decide whether there is anything worth saying.
    """
    if not version_ids:
        return {}

    rows = (
        await session.execute(
            text(_CHAIN_SQL),
            {"version_ids": [str(v) for v in version_ids], "max_depth": MAX_CHAIN_DEPTH},
        )
    ).all()

    return {row.start_id: row.latest_label for row in rows}
