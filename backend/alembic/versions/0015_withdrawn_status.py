"""A version can be withdrawn — removed from search for a reason that is not supersession

#49 established that KDIGO and NICE both forbid exactly what this system is: KDIGO's page 14
reserves all rights against "any information storage and retrieval system", and NICE's Notice
of rights says the same and names AI use and international use as needing a written agreement
and fees. They must not answer a query.

`archived` is the wrong tool. It means "a newer edition replaced this" and stays reachable by
explicit request (#10, include_archived) — a deliberate feature for reading a superseded
guideline on purpose. A licence prohibition is not a supersession, and a withdrawn document
must be unreachable by every path, not just the default one.

So `withdrawn` is a fourth status. Retrieval includes only `active` (and `archived` when
asked), so adding this value hides withdrawn versions by construction — no query changes, no
filter to forget. The chunks and PDF are left in place, so the removal is reversible: if a
licence is granted, one status flip re-indexes nothing. That reversibility is the whole reason
this is a status and not a DELETE.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-17

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside the migration's transaction — the same
    # constraint 0004 hit adding 'pending'. autocommit_block commits the value on its own so
    # anything referencing it afterwards sees it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE version_status ADD VALUE IF NOT EXISTS 'withdrawn'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum, so the type is rebuilt without it. A
    # withdrawn row becomes archived, not active: unreachable-by-default is the safe
    # direction, and re-activating something removed for a licence must be a deliberate act,
    # never a side effect of a downgrade.
    op.execute("UPDATE document_versions SET status = 'archived' WHERE status = 'withdrawn'")

    op.execute("ALTER TYPE version_status RENAME TO version_status_old")
    op.execute("CREATE TYPE version_status AS ENUM ('pending', 'active', 'archived')")
    op.execute(
        "ALTER TABLE document_versions ALTER COLUMN status TYPE version_status "
        "USING status::text::version_status"
    )
    op.execute("DROP TYPE version_status_old")
