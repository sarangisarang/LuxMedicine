"""Versions start pending, not active

Registration (#7) and indexing (#10) cannot be one step: hashing a PDF takes
milliseconds, embedding a 300-page guideline takes minutes. So a version exists before
its chunks do.

With only active|archived, that gap is invisible in the worst way. The version is
active, has no chunks, and retrieval simply never returns it — the clinician reads "no
guidance found" and cannot distinguish that from "your upload is still processing" or
"we failed to read it". Same silent-failure class as a scanned PDF extracting to
nothing.

So a version is `pending` until its chunks are committed, and retrieval only ever looks
at `active`. A half-indexed version is unreachable by construction rather than by
remembering to filter.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot be used in the same transaction that adds it, and
    # the server_default below uses it immediately. autocommit_block steps outside the
    # migration's transaction so the value is committed before anything references it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE version_status ADD VALUE IF NOT EXISTS 'pending' BEFORE 'active'")

    op.alter_column("document_versions", "status", server_default="pending")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum, so the type is rebuilt without it.
    # Any pending rows become archived rather than active: unreachable is the safe
    # direction, since their chunks may not exist.
    op.execute("UPDATE document_versions SET status = 'archived' WHERE status = 'pending'")
    op.alter_column("document_versions", "status", server_default=None)

    op.execute("ALTER TYPE version_status RENAME TO version_status_old")
    op.execute("CREATE TYPE version_status AS ENUM ('active', 'archived')")
    op.execute(
        "ALTER TABLE document_versions ALTER COLUMN status TYPE version_status "
        "USING status::text::version_status"
    )
    op.execute("DROP TYPE version_status_old")
    op.alter_column("document_versions", "status", server_default="active")
    op.alter_column("document_versions", "status", type_=sa.Enum("active", "archived", name="version_status"))
