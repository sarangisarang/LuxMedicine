"""A document version records its licence status and provenance — the upload gate

The self-service upload endpoint is the door 20 copyrighted commercial documents came through on
2026-07-24; a free-text provenance note did not stop them. `license_status` is the structured gate:
only an affirmed licence (public_domain / licensed) lets an upload go active, and anything else is
ingested but quarantined via `status = withdrawn` — present, reversible, never retrievable — until a
human confirms the licence. `provenance_source` records what the uploader said and why.

Server default "unknown" is the fail-safe: an existing row, or one whose licence nobody affirmed,
reads as unaffirmed rather than as fine-to-serve. It does not rewrite the retrievability of existing
rows — that stays decided by `status` — it only sets the gate for new uploads.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column(
            "license_status",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "document_versions",
        sa.Column("provenance_source", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "provenance_source")
    op.drop_column("document_versions", "license_status")
