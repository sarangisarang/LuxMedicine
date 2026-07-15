"""Make erasure actually erase, and record that it happened

**The bug this fixes was in a docstring that asserted the opposite.** `models/query.py`
said `text_hash` "survives redaction, so an audit row can still be matched to a question
presented in evidence, *without us retaining the question*". Measured, that last clause
is false: `text_hash = sha256(question)`, unsalted, and clinical questions come from an
enumerable space — drug names x conditions x a few phrasings. 20 of 20 "erased" questions
were recovered from the surviving hash in 0.3 ms each, pure Python, one core. A GPU rig
does 1e10 sha256/sec, so a space of a trillion realistic phrasings falls in two minutes.

A hash is not a one-way function when its input space is enumerable. It is a lookup key
for the thing it was supposed to erase.

**And there were two oracles, not one.** `prompt_hash` is also in the chain, the prompt
is `"Question:\n{question}\n\nPassages:\n\n{numbered}"`, and the audit row stores
`retrieved_chunk_ids` — so an attacker resolves those chunks, rebuilds the passage block
byte-for-byte, and brute-forces the question again. 20 of 20, from `prompt_hash` alone.
Salting one and stopping would have erased nothing while looking like a fix.

**The fix: a per-query salt, destroyed on erasure.** `hash = sha256(salt || text)`. The
salt lives in `queries` — mutable — and never in `audit_log`. Erasure nulls the text and
the salt together, which leaves `audit_log` byte-identical, so the chain still verifies
while both oracles go dead. Standard crypto-shredding; the novelty here is only which
column the salt may live in.

**What this costs, stated plainly.** After erasure nobody can prove which question was
asked — not a regulator, not the clinician, not us. That is what erasure *is*. The
property that made the hash useful as evidence (anyone holding a candidate can check it)
is the same property that made it brute-forceable. Those are not two features; they are
one feature seen from two sides, and you cannot keep one without the other when the input
space is small. `audit_export` says so per row rather than presenting a hash it can no
longer check.

**This migration is a boundary, and rows before it cannot be fixed.** Existing
`query_hash` values are unsalted and baked into `row_hash`; changing them breaks the
chain from that row on. So every audit row written before this migration is permanently
un-erasable. That is why this lands now, while the corpus is a test fixture — every day
of real data would have been a day of rows nobody can ever erase.

**Why erasure_log is its own table.** An erasure event cannot go in `audit_log`: adding
even one nullable key to `_row_payload` changes `_canonical`'s bytes for every existing
row and fails the whole chain. That payload schema was frozen the day 0001 shipped. So
the events get their own append-only, hash-chained table — same discipline, separate
chain.

**The recursion the issue named.** "Do not record the erased content in the record of the
erasure." `query_id` is a random UUID, not derived from the question, so it is safe to
record. `reason` is an *enum of GDPR legal bases*, not free text — a free-text field is
exactly where someone types "erased the question about X" and re-creates what was just
destroyed. No field exists to hold the content, which is the same move `schemas/answer.py`
makes against clinical recommendations.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, and NULL means two different things by design: not yet salted (a row from
    # before this migration) or salted and then erased. `redacted_at` tells them apart,
    # and audit_export needs the distinction to report honestly.
    op.add_column("queries", sa.Column("text_salt", sa.String(64), nullable=True))

    op.create_table(
        "erasure_log",
        sa.Column("seq", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False, unique=True),
        # A random UUID. Not derived from the question, so recording it does not
        # re-create what was erased — the recursion this issue warns about.
        sa.Column("query_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        # Who performed it. From the token (#30), never from a request body.
        sa.Column("erased_by", sa.String(128), nullable=False),
        # An enum, not free text. See the module docstring.
        sa.Column("legal_basis", sa.String(32), nullable=False),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "legal_basis IN ('consent_withdrawn','no_longer_necessary','unlawful_processing',"
            "'objection_upheld','legal_obligation','operator_request')",
            name="ck_erasure_log_legal_basis",
        ),
    )
    op.create_index("ix_erasure_log_query_id", "erasure_log", ["query_id"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION erasure_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'erasure_log is append-only: % is not permitted', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER erasure_log_no_mutate
        BEFORE UPDATE OR DELETE ON erasure_log
        FOR EACH ROW EXECUTE FUNCTION erasure_log_append_only();
        """
    )
    # Fourth table to need this separately. Row triggers do not fire on TRUNCATE.
    op.execute(
        """
        CREATE TRIGGER erasure_log_no_truncate
        BEFORE TRUNCATE ON erasure_log
        FOR EACH STATEMENT EXECUTE FUNCTION erasure_log_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS erasure_log_no_truncate ON erasure_log")
    op.execute("DROP TRIGGER IF EXISTS erasure_log_no_mutate ON erasure_log")
    op.execute("DROP FUNCTION IF EXISTS erasure_log_append_only()")
    op.drop_index("ix_erasure_log_query_id", table_name="erasure_log")
    op.drop_table("erasure_log")
    op.drop_column("queries", "text_salt")
