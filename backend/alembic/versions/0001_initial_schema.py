"""Initial schema: documents, versions, chunks, queries, append-only audit_log

Revision ID: 0001
Revises:
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = get_settings().embedding_dim
APP_DB_ROLE = get_settings().app_db_role


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("issuing_org", sa.String(128), nullable=False),
        sa.Column("region", sa.String(64)),
        sa.Column("guideline_type", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_documents_issuing_org", "documents", ["issuing_org"])
    op.create_index("ix_documents_region", "documents", ["region"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(64), nullable=False),
        sa.Column("published_at", sa.Date()),
        sa.Column(
            "status",
            sa.Enum("active", "archived", name="version_status"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("file_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "superseded_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "version_label", name="uq_document_version"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_status", "document_versions", ["status"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("page", sa.Integer),
        sa.Column("section", sa.Text),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_chunk_ordinal"),
    )
    op.create_index("ix_chunks_document_version_id", "chunks", ["document_version_id"])

    # HNSW over cosine distance. Built now while the table is empty — cheap here,
    # expensive later. Revisit the operator class if you switch embedding models.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    op.create_table(
        "queries",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        # Nullable by design: NULL means redacted under a GDPR erasure request.
        sa.Column("text", sa.Text),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("language", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("redacted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_queries_actor_id", "queries", ["actor_id"])
    op.create_index("ix_queries_text_hash", "queries", ["text_hash"])

    op.create_table(
        "audit_log",
        sa.Column("seq", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column(
            "query_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("queries.id"),
            nullable=False,
        ),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column(
            "retrieved_chunk_ids",
            sa.dialects.postgresql.ARRAY(sa.dialects.postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("response", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_query_id", "audit_log", ["query_id"])

    # --- Append-only enforcement -------------------------------------------------
    # This is the part that turns a log into an audit trail. Without it, "immutable"
    # is a naming convention that any UPDATE can violate.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_log is append-only: % is not permitted', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_mutate
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
        """
    )
    # TRUNCATE bypasses row-level triggers entirely, so it needs its own.
    op.execute(
        """
        CREATE TRIGGER audit_log_no_truncate
        BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
        """
    )

    # Defence in depth: the trigger stops accidents, the grants stop a compromised
    # app role. A superuser can still drop the trigger — which is why the hash chain
    # exists on top of both. Skipped when APP_DB_ROLE is unset (local dev).
    if APP_DB_ROLE:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_DB_ROLE}') THEN
                    REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {APP_DB_ROLE};
                    GRANT SELECT, INSERT ON audit_log TO {APP_DB_ROLE};
                ELSE
                    RAISE WARNING 'APP_DB_ROLE % does not exist; skipped REVOKE on audit_log', '{APP_DB_ROLE}';
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_mutate ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    op.drop_table("audit_log")
    op.drop_table("queries")
    op.drop_table("chunks")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.execute("DROP TYPE IF EXISTS version_status")
