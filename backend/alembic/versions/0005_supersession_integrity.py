"""Supersession cannot point at itself, or at another guideline

Two of the three ways a supersession edge can be wrong are decidable from a single row
plus a join, so they belong in the database rather than in a code path someone has to
remember.

**Self-reference** — a version superseding itself. One CHECK, absolute.

**Cross-document** — a version superseding an edition of a *different* guideline. Not
in the original brief, and more dangerous than a cycle: a cycle produces a loop
somebody notices, while this produces a confident wrong answer. #17 would tell a
cardiologist their heart-failure guideline is superseded and hand them an oncology
document. The composite foreign key makes it impossible rather than unlikely — the
successor must share the predecessor's document_id, enforced by Postgres.

The third way — cycles — needs multi-row reasoning and cannot be a constraint. It is
handled in app/services/supersession.py by an invariant that makes cycles structurally
impossible, plus a lock, because the invariant's check races.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-15

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_version_not_self_superseding",
        "document_versions",
        "superseded_by IS NULL OR superseded_by <> id",
    )

    # Redundant as a uniqueness claim — id is already the primary key — but a composite
    # foreign key needs a matching unique target, and this is what lets the FK below
    # carry document_id along.
    op.create_unique_constraint(
        "uq_version_id_document", "document_versions", ["id", "document_id"]
    )

    # The successor must belong to the same document. The old single-column FK allowed
    # any version anywhere.
    op.drop_constraint("document_versions_superseded_by_fkey", "document_versions", type_="foreignkey")
    op.create_foreign_key(
        "fk_superseded_by_same_document",
        "document_versions",
        "document_versions",
        ["superseded_by", "document_id"],
        ["id", "document_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_superseded_by_same_document", "document_versions", type_="foreignkey")
    op.create_foreign_key(
        "document_versions_superseded_by_fkey",
        "document_versions",
        "document_versions",
        ["superseded_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("uq_version_id_document", "document_versions", type_="unique")
    op.drop_constraint("ck_version_not_self_superseding", "document_versions", type_="check")
