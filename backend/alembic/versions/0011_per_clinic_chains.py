"""One chain per clinic, because a global chain cannot be isolated

#31 asked whether row-level security beats `WHERE clinic_id = ?`. Measuring it answered
that (RLS holds when the WHERE is forgotten; a forgotten RLS context returns zero rows
rather than someone else's) and turned up a collision nobody had noticed between #29 and
#31:

**A global hash chain and per-tenant RLS are incompatible.** Measured on real Postgres: a
tenant restricted to their own rows sees seqs [1, 3, 5] of a five-row chain, and walking
it reports a break at seq=3. Every row they cannot see is a link they cannot follow, so
`verify_chain()` would cry tampering on a perfectly intact chain. The alternative —
exempting audit_log from RLS — puts the isolation back in `WHERE`, in the one table with
the most sensitive data.

The gaps leak, too. Seeing [1, 3, 5] tells clinic-a that rows 2 and 4 exist: another
tenant's activity volume, readable from the holes.

So the chain is per clinic. `prev_hash` links only within a clinic, each starts at its own
genesis, and a tenant can verify their whole trail with no access to anyone else's. That
is what a chain is *for* — a chain only the operator can verify is, from the tenant's
side, just a log with extra steps.

**A consequence worth having:** the advisory lock is now per clinic. It used to serialise
every append in the entire system against every other; two clinics never had any reason
to contend, and now they do not.

**A property given up, honestly:** ordering across clinics is no longer proven. Nothing
asked for it.

**Why this is possible today and would not be tomorrow.** `_row_payload` gains `clinic_id`,
and adding any key to that payload changes `_canonical`'s bytes for every row already
written — every one of them would fail verification. It is safe here only because there
are no rows: the dev database holds zero, and the test database is rebuilt from migrations
every session. After a real deployment this same change would need a per-row payload
version, and the payload could never simply be edited again. `tests/test_audit_db.py` now
pins the payload's exact key set so that the next person to add a field finds a failing
test and this note, rather than a silently broken chain. Same shape of boundary as 0010's,
and the same reason it lands now.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Opaque, like actor_id — an identifier from the token, never a name. No FK to a
    # clinics table: there isn't one yet, and inventing it here would be inventing a
    # registration flow nobody has designed.
    #
    # NOT NULL everywhere it identifies the asker. A row that belongs to no clinic is a
    # row RLS cannot place, and "no clinic" must not become a hiding place.
    op.add_column("queries", sa.Column("clinic_id", sa.String(128), nullable=False))
    op.create_index("ix_queries_clinic_id", "queries", ["clinic_id"])

    op.add_column("audit_log", sa.Column("clinic_id", sa.String(128), nullable=False))
    # The chain is walked per clinic and in seq order; this is the index that walk uses.
    op.create_index("ix_audit_log_clinic_seq", "audit_log", ["clinic_id", "seq"])

    op.add_column("chain_checkpoints", sa.Column("clinic_id", sa.String(128), nullable=False))
    op.create_index(
        "ix_chain_checkpoints_clinic", "chain_checkpoints", ["clinic_id", "verified_at"]
    )

    op.add_column("erasure_log", sa.Column("clinic_id", sa.String(128), nullable=False))
    op.create_index("ix_erasure_log_clinic_seq", "erasure_log", ["clinic_id", "seq"])

    # Nullable here, and only here. NULL means a published guideline — ESC, EASD, ESMO —
    # which belongs to everyone; every clinic must retrieve those or the product does
    # nothing. NOT NULL means a clinic's own uploaded protocol, which is theirs alone.
    # This is the one place "no clinic" is a real answer rather than a gap.
    op.add_column("documents", sa.Column("clinic_id", sa.String(128), nullable=True))
    op.create_index("ix_documents_clinic_id", "documents", ["clinic_id"])

    # `uq_document_org_title` was global, and that is a cross-tenant collision *and* a
    # leak: clinic-b uploading its own "Sepsis Protocol" would be rejected because
    # clinic-a already has one, and the error would tell them so. The constraint has to
    # include the clinic.
    #
    # NULLS NOT DISTINCT (Postgres 15+; measured on 17.10) because a plain UNIQUE treats
    # NULLs as distinct, so with clinic_id NULL for published guidelines the constraint
    # would simply stop firing for them — two identical ESC entries, both accepted,
    # silently. Verified both ways before relying on it: duplicate public documents are
    # still rejected, and two clinics with the same org+title no longer collide.
    op.drop_constraint("uq_document_org_title", "documents", type_="unique")
    op.create_unique_constraint(
        "uq_document_clinic_org_title",
        "documents",
        ["clinic_id", "issuing_org", "title"],
        postgresql_nulls_not_distinct=True,
    )

    # row_hash was globally unique. It still can be: two clinics producing an identical
    # row_hash would need identical payloads including clinic_id, which cannot happen.
    # Left alone deliberately — it is the backstop against a forked chain (#4).


def downgrade() -> None:
    op.drop_constraint("uq_document_clinic_org_title", "documents", type_="unique")
    op.create_unique_constraint(
        "uq_document_org_title", "documents", ["issuing_org", "title"]
    )
    op.drop_index("ix_documents_clinic_id", table_name="documents")
    op.drop_column("documents", "clinic_id")
    op.drop_index("ix_erasure_log_clinic_seq", table_name="erasure_log")
    op.drop_column("erasure_log", "clinic_id")
    op.drop_index("ix_chain_checkpoints_clinic", table_name="chain_checkpoints")
    op.drop_column("chain_checkpoints", "clinic_id")
    op.drop_index("ix_audit_log_clinic_seq", table_name="audit_log")
    op.drop_column("audit_log", "clinic_id")
    op.drop_index("ix_queries_clinic_id", table_name="queries")
    op.drop_column("queries", "clinic_id")
