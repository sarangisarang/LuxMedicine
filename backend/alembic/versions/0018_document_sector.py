"""A document belongs to a sector, and retrieval never crosses one

The corpus is no longer only clinical guidance: German construction and procurement law
(HOAI, VgV, GWB) is a second body of source text. Both are statute-shaped, both are
quoted verbatim, and both are retrieved by the same embedding — which is exactly the
problem. "Leistungsphase" and "Phase III" are not far apart in vector space, and an
extractive system quotes whatever it is handed. A stray §-paragraph in a clinical result
set would come back verbatim, correctly cited, and completely wrong.

So the separation is a WHERE clause, not a prompt instruction. `sector` is filtered in
both retrieval paths (vector and hybrid) with no way to ask for "all sectors".

Default 'medical': every row that exists when this runs is a clinical guideline, and
saying so in the DDL is better than a backfill that can be skipped. NOT NULL for the same
reason retrieval filters unconditionally — a NULL sector is a document no question can
reach, and it would fail silently.

Indexed because every single search filters on it.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("sector", sa.String(32), server_default="medical", nullable=False),
    )
    op.create_index("ix_documents_sector", "documents", ["sector"])


def downgrade() -> None:
    op.drop_index("ix_documents_sector", table_name="documents")
    op.drop_column("documents", "sector")
