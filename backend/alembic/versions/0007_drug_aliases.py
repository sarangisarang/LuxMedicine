"""Brand-name to generic aliases, for query expansion

Measured against multilingual-e5-large before writing anything. ROADMAP #16 predicted
the gap was cross-lingual jargon. It is not:

    "ჰიპერტენზიის მკურნალობა"    -> hypertension passage, margin  0.0738  ok
    "მაღალი წნევის მკურნალობა"    -> hypertension passage, margin  0.0804  ok
    "Hypertonie Behandlung"       -> hypertension passage, margin  0.0740  ok
    "лечение высокого давления"   -> hypertension passage, margin  0.1160  ok
    "ACEi first line"             -> hypertension passage, margin  0.0803  ok

The model already bridges clinical terms, lay phrasing and common abbreviations across
languages. What it cannot do is brand names — and it fails *below chance*:

    "Renitec dose"  -> enalapril 0.8254 | metformin 0.8264  margin -0.0010  DECOY WINS
    "Vasotec dose"  -> enalapril 0.8171 | metformin 0.8193  margin -0.0022  DECOY WINS

Renitec and Vasotec are enalapril. A clinician asking about a patient's blood-pressure
medicine by the name on the box is handed a passage about a diabetes drug, ranked first.
That is not a retrieval-quality problem, it is a wrong-drug problem, and no amount of
embedding gets there: the model has no way to know the association exists.

Aliases are a table, not a constant in code, because they are data: thousands of them,
national, changing, and different per clinic (#31). `source` is not bookkeeping — a
wrong alias silently answers about the wrong drug, so every row must say where it came
from and be answerable to someone.

Query-side only. The corpus is never rewritten: expanding stored text would corrupt what
citations quote and #19 validates, the same reason de-hyphenation was refused in #8.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drug_aliases",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        # Stored lowercase; the unique constraint is what stops one brand mapping to two
        # generics, which would be a silent, permanent wrong answer.
        sa.Column("alias", sa.String(128), nullable=False, unique=True),
        sa.Column("generic_name", sa.String(128), nullable=False),
        # Provenance. A mapping nobody can trace is a mapping nobody can correct.
        sa.Column("source", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("alias = lower(alias)", name="ck_alias_lowercase"),
        sa.CheckConstraint("alias <> lower(generic_name)", name="ck_alias_not_self"),
    )
    op.create_index("ix_drug_aliases_generic_name", "drug_aliases", ["generic_name"])


def downgrade() -> None:
    op.drop_table("drug_aliases")
