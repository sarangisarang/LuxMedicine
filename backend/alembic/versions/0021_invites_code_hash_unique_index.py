"""invites.code_hash is a unique index, not a constraint-plus-index

The model declares `code_hash` as `unique=True, index=True`, which SQLAlchemy renders as a single
unique index `ix_invites_code_hash`. Migration 0017 instead created a named UniqueConstraint
`uq_invites_code_hash` alongside a *non-unique* `ix_invites_code_hash`. Both enforce uniqueness, so
production was never wrong — but `alembic check` compares the model against the migrations and sees a
constraint the model never declares and an index whose uniqueness disagrees. This mismatch only
reached CI now, because the invite feature (#51) was committed locally long before it was pushed.

Reconcile the schema to what the model says: drop the constraint and make the index unique, reusing
0017's exact names so it applies cleanly on top of a database that already ran 0017 (production).

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-24

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_invites_code_hash", "invites", type_="unique")
    op.drop_index("ix_invites_code_hash", table_name="invites")
    op.create_index("ix_invites_code_hash", "invites", ["code_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_invites_code_hash", table_name="invites")
    op.create_index("ix_invites_code_hash", "invites", ["code_hash"])
    op.create_unique_constraint("uq_invites_code_hash", "invites", ["code_hash"])
