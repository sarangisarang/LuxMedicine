"""A chunk can be flagged as a bibliography entry, excluded from retrieval (#50)

A reference list is not guidance, but its entries carry clinical keywords in paper titles, so
they win retrieval on lexical and embedding match and bury the guidance that answers a
question — measured: NHLBI is ~34-38% bibliography, and contraception-postpartum's top-11 was
nine citations. This adds the flag; a backfill (app/cli/mark_references.py) sets it on existing
chunks and retrieval excludes it, the same shape as the version-status filter.

Default false, so every existing chunk stays searchable until the backfill runs and every new
chunk is searchable unless ingestion marks it. Not deleted — the chunk is still real text a
citation could resolve to; it simply must not source an answer.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-17

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column("is_reference", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chunks", "is_reference")
