"""Chunks carry a page span, not a page

A page break is a typographic accident, not a semantic boundary. Splitting chunks at
one would separate a dose from the condition qualifying it — "the target dose is" |
"20 mg twice daily" — which is the clinical hazard #9 exists to avoid. So a chunk may
span pages, and a citation must be able to say "pp. 45-46".

page_start / page_end rather than an integer array: a chunk is a contiguous run of
text, so its pages are always a contiguous range. An array would let the schema express
{45, 91}, which no extraction can produce and every reader would have to consider.

Both are 1-based **PDF page indices**, not the folio printed on the page. See the note
in app/services/extraction.py — the difference is real and it is not hidden.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # chunks is empty — nothing has been ingested — so this is a straight replacement
    # rather than a backfill. It will not be after #10 lands.
    op.drop_column("chunks", "page")
    op.add_column("chunks", sa.Column("page_start", sa.Integer(), nullable=False))
    op.add_column("chunks", sa.Column("page_end", sa.Integer(), nullable=False))
    op.create_check_constraint("ck_chunk_page_span", "chunks", "page_end >= page_start")


def downgrade() -> None:
    op.drop_constraint("ck_chunk_page_span", "chunks", type_="check")
    op.drop_column("chunks", "page_end")
    op.drop_column("chunks", "page_start")
    op.add_column("chunks", sa.Column("page", sa.Integer(), nullable=True))
