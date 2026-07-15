"""Full-text vector on chunks, for the lexical half of hybrid search

Measured before written. multilingual-e5-large was asked for five ACE inhibitors whose
passages differ only by drug name and dose:

    enalapril query -> 0.9126 enalapril
                       0.8736 lisinopril     <- a different drug
                       0.8693 perindopril
                       0.8666 captopril
                       0.8585 ramipril

Top-1 is right, every time. So the claim in ROADMAP #15 — that the embedding drifts to
the wrong drug — is wrong as written, and is corrected there.

The real problem is the spread. All five sit within 0.055 of each other, so any top-k
above 1 returns four passages about drugs the clinician did not ask about, each looking
as relevant as the right one. Lexical search separates them categorically: the word
"enalapril" is present or it is not.

Generated, not application-maintained: a tsvector computed in Python drifts the moment
content is updated without it. Postgres cannot let it. The two-argument to_tsvector is
required for that — the single-argument form is STABLE, not IMMUTABLE, and generated
columns will not take it.

'english' is the corpus's language, not the users'. Queries arrive in 18 languages and
lexical search contributes nothing to most of them — a Georgian query shares no tokens
with English text. That is expected: the vector half carries those, and the lexical
half is here for drug names and doses, which are Latin-script and numeric in every
language's query. If non-English guidelines are ever ingested, this becomes a
per-document config rather than a constant.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-15

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT_SEARCH_CONFIG = "english"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('{TEXT_SEARCH_CONFIG}', content)) STORED
        """
    )
    op.execute("CREATE INDEX ix_chunks_content_tsv ON chunks USING gin (content_tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN content_tsv")
