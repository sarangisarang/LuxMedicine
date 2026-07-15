"""Checkpoints: bound a chain break in time instead of merely detecting it

`verify_chain` answers "is the chain intact". When it says no, the next question is *when
did this happen* — and without a record of past verifications the only honest answer is
"sometime since genesis". A checkpoint says: at this time, the chain was intact through
seq N. A break found below N therefore happened after that time.

**A checkpoint in this database is worth exactly what this database's integrity is
worth.** Anyone who can rewrite audit_log can rewrite this table too, and then the bound
is whatever they say it is. The append-only trigger below raises the cost — they must now
defeat two guards rather than one — and raises it no further than that. The bound becomes
real when a checkpoint is *copied somewhere else*: a WORM bucket, an append-only log
service, a printout in a drawer. That is a deployment decision, so the mechanism records
and exports; the shipping is the operator's.

Append-only for the same reason audit_log is: a checkpoint an attacker can delete is a
bound they can remove, and the whole point is to narrow the window they have to explain.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chain_checkpoints",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # The chain tail at the moment of verification. No foreign key to audit_log.seq
        # on purpose: this is a record of what was observed, and it must survive to
        # describe a chain that has since been damaged.
        sa.Column("verified_through_seq", sa.BigInteger, nullable=False),
        sa.Column("tail_row_hash", sa.String(64), nullable=False),
        sa.Column("entries_verified", sa.BigInteger, nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        # Who or what ran it. A checkpoint nobody can attribute is a checkpoint nobody
        # can question.
        sa.Column("verified_by", sa.String(128), nullable=False),
    )
    op.create_index(
        "ix_chain_checkpoints_verified_at", "chain_checkpoints", ["verified_at"]
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION chain_checkpoints_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'chain_checkpoints is append-only: % is not permitted', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER chain_checkpoints_no_mutate
        BEFORE UPDATE OR DELETE ON chain_checkpoints
        FOR EACH ROW EXECUTE FUNCTION chain_checkpoints_append_only();
        """
    )
    # TRUNCATE bypasses row-level triggers — the third time this has needed its own guard.
    op.execute(
        """
        CREATE TRIGGER chain_checkpoints_no_truncate
        BEFORE TRUNCATE ON chain_checkpoints
        FOR EACH STATEMENT EXECUTE FUNCTION chain_checkpoints_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS chain_checkpoints_no_truncate ON chain_checkpoints")
    op.execute("DROP TRIGGER IF EXISTS chain_checkpoints_no_mutate ON chain_checkpoints")
    op.execute("DROP FUNCTION IF EXISTS chain_checkpoints_append_only()")
    op.drop_table("chain_checkpoints")
