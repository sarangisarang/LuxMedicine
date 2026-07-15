"""Row-level security: the tenant boundary the application cannot forget

#31 asked whether RLS beats `WHERE clinic_id = ?`. Six scenarios were measured on real
Postgres before any of this was written:

  1. WHERE written correctly ................ 1 row
  2. the same query, WHERE forgotten ........ RLS held: 1 row
  3. SET (not SET LOCAL) on a pooled conn ... LEAKED: the next request, which set
                                              nothing, read the previous tenant's rows
  4. SET LOCAL on a pooled conn ............. transaction-scoped, no leak
  5. no tenant context at all ............... fails closed, 0 rows
  6. superuser / BYPASSRLS .................. every policy skipped, and
                                              FORCE ROW LEVEL SECURITY does not help

(2) is the case for RLS: a forgotten WHERE is a leak, a forgotten RLS context is an empty
result. **So no query in this codebase filters by clinic_id.** Writing both would hide
the answer to "is RLS actually on" — if the WHERE is always there, nothing ever finds out.
The isolation tests query with no filter at all, which is the only way they mean anything.

(6) is why this migration is mostly about roles. docker-compose's POSTGRES_USER is the
container superuser with rolbypassrls; write policies, run tests as that role, and RLS is
silently inactive while every test passes. FORCE ROW LEVEL SECURITY binds the table
*owner* and does nothing to a superuser. The first isolation test asserts the app role is
not a superuser, because without that the rest are theatre.

**Fail-closed is silent here, and that is handled in the application, not in the policy.**
A strict `current_setting('app.clinic_id')` looks like the fix — raise instead of
returning nothing — and measurement says it is not: strict raises on a connection that
never set the GUC, but after one SET LOCAL that same connection registers the parameter
and afterwards returns `''` instead of raising. Under a pool the first request disarms it
for every later one, so a test on a fresh connection proves a behaviour production never
has. `app/db/tenancy.py` refuses to open a transaction with no clinic bound; that is where
the noise lives.

**What is NOT protected, said plainly.** These policies bind the app role. They do not
bind a superuser, they do not bind the migration role, and a `BYPASSRLS` grant switches
them off wholesale. That is the same shape as 0001's append-only triggers, which a
superuser can drop — and the same answer: the hash chain sits above both, and clinic_id is
inside the hashed payload (0011), so a row moved between tenants by someone who bypassed
all of this still breaks the chain. Isolation is enforced by RLS and *evidenced* by the
chain. Two mechanisms, different failure modes, on purpose.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-15

"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables whose rows belong to exactly one clinic.
TENANT_TABLES = ["queries", "audit_log", "chain_checkpoints", "erasure_log"]

# `documents` is different: clinic_id NULL means a published guideline that every clinic
# must be able to retrieve, or the product does nothing.
#
# chunks and document_versions carry no clinic_id of their own and are reached through
# documents. Their policies join up to it rather than denormalising the column, because a
# copy is a thing that can disagree — and a chunk whose clinic_id says one thing while its
# document says another is a leak with a paper trail that looks fine.
GUC = "app.clinic_id"


def upgrade() -> None:
    conn = op.get_bind()

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE, so the table owner is bound too. Not a superuser — nothing binds a
        # superuser — but it closes the case where the app happens to own its tables.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # missing_ok=true deliberately: the strict form is inconsistent under pooling
        # (see the module docstring). Fails closed either way; the loud failure is in
        # app/db/tenancy.py.
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (clinic_id = current_setting('{GUC}', true))
            WITH CHECK (clinic_id = current_setting('{GUC}', true))
            """
        )

    # WITH CHECK as well as USING: without it a tenant can SELECT only their own rows and
    # still INSERT a row stamped with someone else's clinic — writing into a chain they
    # cannot read. The chain would then fork, and the tenant who owns it would be the one
    # holding a broken trail.

    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON documents
        USING (clinic_id IS NULL OR clinic_id = current_setting('{GUC}', true))
        WITH CHECK (clinic_id IS NULL OR clinic_id = current_setting('{GUC}', true))
        """
    )

    for table, fk in [("document_versions", "document_id"), ("chunks", "document_version_id")]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # These two ask one question: "is the document this belongs to visible to me?"
    #
    # They do NOT repeat the clinic check, and that is a correction rather than a
    # shortcut. The first draft repeated it, and mutating it away changed nothing —
    # because RLS applies to *every* table reference, including ones inside another
    # policy's expression. `SELECT 1 FROM documents` here is itself filtered by
    # documents' own policy, so the repeated condition was unreachable: protection that
    # cannot fail is protection that cannot be tested, and a copy of documents' rule
    # living inside chunks' rule is a copy that can drift away from it. Same reason
    # chunks has no clinic_id column of its own.
    #
    # Verified before relying on it: as clinic-beta, `SELECT count(*) FROM documents`
    # returns only beta's, and alpha's rows are unreachable from inside this expression.
    op.execute(
        """
        CREATE POLICY tenant_isolation ON document_versions
        USING (EXISTS (
            SELECT 1 FROM documents d WHERE d.id = document_versions.document_id
        ))
        WITH CHECK (EXISTS (
            SELECT 1 FROM documents d WHERE d.id = document_versions.document_id
        ))
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON chunks
        USING (EXISTS (
            SELECT 1 FROM document_versions v WHERE v.id = chunks.document_version_id
        ))
        WITH CHECK (EXISTS (
            SELECT 1 FROM document_versions v WHERE v.id = chunks.document_version_id
        ))
        """
    )

    # drug_aliases is a shared reference table with no tenant. Left un-policied rather
    # than given an "everyone" policy: an empty policy list on an RLS-enabled table denies
    # everything, and a permissive policy would be a lie about a table that has no tenant
    # to isolate. It holds published brand->generic mappings and nothing personal.

    role = _app_role(conn)
    if role:
        op.execute(f'GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO "{role}"')
        op.execute(f'GRANT UPDATE, DELETE ON queries, documents, document_versions, chunks TO "{role}"')
        op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
        # audit_log, erasure_log and chain_checkpoints get INSERT only — 0001 already
        # revokes UPDATE/DELETE on audit_log from this role, and the grant above must not
        # quietly hand it back.
        op.execute(f'REVOKE UPDATE, DELETE ON audit_log, erasure_log, chain_checkpoints FROM "{role}"')


def _app_role(conn) -> str | None:
    """The least-privileged role the API connects as, if configured.

    Empty in local dev, where everything runs as the superuser — which is exactly the
    configuration in which none of the above does anything. `tests/test_rls.py` connects
    as a real non-superuser role and asserts that first.
    """
    from app.core.config import get_settings

    role = get_settings().app_db_role.strip()
    if not role:
        return None
    exists = conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
    ).scalar_one_or_none()
    return role if exists else None


def downgrade() -> None:
    for table in [
        "chunks",
        "document_versions",
        "documents",
        *TENANT_TABLES,
    ]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
