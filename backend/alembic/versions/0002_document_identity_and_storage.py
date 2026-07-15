"""Document natural key + PDF storage location

Two gaps that #7 exposed.

**UNIQUE(issuing_org, title)** — nothing stopped two `documents` rows describing the
same guideline. Versions would then scatter across both, and the damage lands where it
is hardest to see: supersession (#12) marks the wrong predecessor, and the staleness
warning (#17) goes quiet because the 2021 edition's successor is filed under a
different document. A clinician would be told nothing, which reads exactly like being
told the guideline is current.

**storage_uri** — hashing the bytes and discarding them proves nothing later: there is
nothing left to compare the hash against, no text to extract (#8), and no page to show
(#35). Storage is content-addressed, so the path derives from the hash and identical
bytes cannot occupy two locations.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_document_org_title", "documents", ["issuing_org", "title"])
    op.add_column("document_versions", sa.Column("storage_uri", sa.Text(), nullable=False))


def downgrade() -> None:
    op.drop_column("document_versions", "storage_uri")
    op.drop_constraint("uq_document_org_title", "documents", type_="unique")
