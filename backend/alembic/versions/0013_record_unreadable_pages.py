"""Unreadable pages survive ingestion, so the hole is visible to whoever reads the answer

#41 detected the damage and #41's first commit threw it away. `IndexResult.damaged_pages`
lives for the length of one function call and then nothing remembers. The chunks were
correctly refused; the corpus simply became quieter, and quieter is exactly what a
clinician cannot see.

**The failure this closes.** KDIGO 2012 has 18 pages whose formulae extracted as
`141(cid:2)min(SCr/k,1)...`. Those chunks are now refused — correctly. So a clinician
asking about the CKD-EPI equation gets the surrounding prose about eGFR estimation and
*not the equation*, and nothing in the answer says a page is missing. The answer looks
complete. That is the same confusion `NoAnswerReason` (#20) exists to prevent — "the
guideline does not say" and "we could not read the page where it says it" must never
produce the same output — and the guard I wrote in the last commit produced exactly it.

The damage was loud to the operator running ingestion and silent to the clinician reading
the result. Half a fix, and the half that was missing is the half that matters.

So it lands on the version: retrieval joins to it, `SourceGroup` can say it, and the audit
export records it. `int[]` rather than a count — "pages 7, 8, 10" lets someone open the
PDF there and judge; "3 pages" does not.

Nullable, defaulting to empty: a version indexed before this migration has no measurement,
which is not the same as having no damage. Empty means "checked, clean"; NULL means
"nobody looked". Those are different and the export says which.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("unreadable_pages", postgresql.ARRAY(sa.Integer), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "unreadable_pages")
