"""A passage the audit trail cites cannot be deleted

#27 surfaced the hole: `audit_log.retrieved_chunk_ids` is a plain `uuid[]` with no
foreign key, and `documents` -> `document_versions` -> `chunks` cascades on delete. So
`DELETE FROM documents` silently removed passages an audit row named, and the trail lost
the ability to show what the system read.

**What this is not.** Deleting a chunk never broke the hash chain — the chain hashes
audit rows and has never touched the corpus, so every verification kept passing. Nor did
it affect a clinician: a deleted chunk is simply not retrieved. The damage is to
reconstruction, which is the one thing the trail exists for. A row saying "we relied on
chunk X" beside an X that no longer exists is a record of nothing.

`audit_log.query_id` was already safe — it is a real foreign key with no ON DELETE, so
Postgres already refuses to drop a cited query. This closes the same gap for chunks, the
only cited thing that was reachable.

**Why a trigger rather than a foreign key.** A foreign key cannot reference an element of
an array. The alternatives were a junction table (a schema change to a chain-hashed
column's meaning, for no gain) or accepting the hole. The trigger mirrors 0001's
append-only triggers, and for the same reason: "only reachable via direct SQL" is exactly
the threat those exist for, not a mitigation.

**The escape hatch is deliberate and already honest.** UPDATE is not blocked, so a
copyright takedown is served by clearing the passage's text — the row and its id survive,
so the trail still resolves, and #27's export reports the quote no longer matching as
"the corpus changed". That is the truth in that situation. GDPR is not the reason to
reach for it: a published guideline is not personal data, and the erasable side of that
line is `queries` (#5), which already works.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-15

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The trigger below runs a containment check on every chunk delete. Without this the
    # check is a sequential scan of audit_log, and re-ingesting a corpus would degrade
    # from slow to unusable as the trail grows.
    op.execute(
        "CREATE INDEX ix_audit_log_retrieved_chunk_ids "
        "ON audit_log USING gin (retrieved_chunk_ids)"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION chunk_is_cited_by_audit() RETURNS trigger AS $$
        BEGIN
            -- @> rather than = ANY(): the containment operator is what the GIN index
            -- above can answer. = ANY() would work and would scan.
            IF EXISTS (
                SELECT 1 FROM audit_log
                WHERE retrieved_chunk_ids @> ARRAY[OLD.id]
            ) THEN
                RAISE EXCEPTION
                    'chunk % is cited by the audit trail and cannot be deleted', OLD.id
                    USING ERRCODE = 'foreign_key_violation',
                          HINT = 'To honour a takedown, clear the passage text instead — '
                                 'the row must survive for the trail to resolve.';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER chunks_no_delete_when_cited
        BEFORE DELETE ON chunks
        FOR EACH ROW EXECUTE FUNCTION chunk_is_cited_by_audit();
        """
    )

    # TRUNCATE bypasses row-level triggers, exactly as it does on audit_log (0001). It
    # cannot check per row, so it refuses whenever any chunk is cited at all — the
    # conservative reading, and TRUNCATE is not how a legitimate takedown is served.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION chunks_truncate_guard() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM audit_log
                WHERE array_length(retrieved_chunk_ids, 1) > 0
            ) THEN
                RAISE EXCEPTION
                    'chunks cannot be truncated: the audit trail cites passages in this table'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER chunks_no_truncate_when_cited
        BEFORE TRUNCATE ON chunks
        FOR EACH STATEMENT EXECUTE FUNCTION chunks_truncate_guard();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS chunks_no_truncate_when_cited ON chunks")
    op.execute("DROP TRIGGER IF EXISTS chunks_no_delete_when_cited ON chunks")
    op.execute("DROP FUNCTION IF EXISTS chunks_truncate_guard()")
    op.execute("DROP FUNCTION IF EXISTS chunk_is_cited_by_audit()")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_retrieved_chunk_ids")
