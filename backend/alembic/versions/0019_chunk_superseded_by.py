"""A chunk can be superseded by a better reading of the same printed row

`augment_tables` is insert-only and must stay that way: migration 0008 forbids deleting a
chunk the audit trail cites, because a row saying "we relied on chunk X" beside a missing X
is a record of nothing. The consequence is that every fix to the extractor lays a corrected
row down BESIDE the flawed one, and both remain retrievable.

After the label-completion fix that was 88 truncated rows sitting next to their completed
twins — so "ii. Systolic ≥160 mm Hg or" could still out-rank "Systolic ≥160 mm Hg or
diastolic ≥100 mm Hg" and give a clinician half a threshold, quoted verbatim and cited
correctly.

Deleting the old rows was the obvious move and the wrong one: it needs an exception for the
cited ones (the trigger refuses them), so those would stay live and searchable — the exact
hole the operation was meant to close. Superseding closes it for every row uniformly: the
chunk stays in place for the trail, leaves retrieval, and can be un-marked.

A pointer, not a flag, so the pairing that retired a row is recorded and can be re-checked.
ON DELETE SET NULL: if the replacement is ever removed, the original becomes current again,
which is the truthful state rather than a dangling reference.

Indexed because both retrieval paths filter on it on every query.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column("superseded_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chunks_superseded_by",
        "chunks",
        "chunks",
        ["superseded_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_chunks_superseded_by", "chunks", ["superseded_by"])


def downgrade() -> None:
    op.drop_index("ix_chunks_superseded_by", table_name="chunks")
    op.drop_constraint("fk_chunks_superseded_by", "chunks", type_="foreignkey")
    op.drop_column("chunks", "superseded_by")
