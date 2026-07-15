"""Query expansion for brand names (#16).

Applied to the query, never to the corpus. Rewriting stored text would corrupt what
citations quote and #19 validates as faithful — the same reason de-hyphenation was
refused in #8. A query is disposable; a chunk is evidence.

Applied to *both* halves of the hybrid, which was not the original plan. The lexical
half obviously needs it — "renitec" is not a token in any guideline. But the vector half
needs it just as much, and that only became clear from the measurement: this is not a
tokenisation gap the embedding quietly covers, it is a fact the model was never taught.
So the generic name is added to the text before it is embedded, not just before it is
tokenised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alias import DrugAlias


@dataclass(frozen=True)
class ExpandedQuery:
    original: str

    # What actually gets embedded and tokenised. The original text with each recognised
    # brand's generic appended — the brand is kept, not replaced, because a guideline may
    # mention it too and dropping it would trade one blind spot for another.
    expanded: str

    # brand -> generic, for every substitution made. Surfaced rather than hidden: without
    # it, "why did asking about Renitec return enalapril?" has no answer, and an expansion
    # nobody can inspect is an expansion nobody can correct.
    applied: dict[str, str]

    @property
    def was_expanded(self) -> bool:
        return bool(self.applied)


async def load_aliases(session: AsyncSession) -> dict[str, str]:
    """alias -> generic_name, lowercased.

    Small enough to hold in memory (thousands of rows at most) and read on every search;
    caching is a later problem and a stale cache here answers about the wrong drug.
    """
    rows = (await session.execute(select(DrugAlias.alias, DrugAlias.generic_name))).all()
    return {row.alias: row.generic_name for row in rows}


def expand_query(text: str, aliases: dict[str, str]) -> ExpandedQuery:
    """Append the generic name for every brand mentioned.

    Word boundaries, not substrings. "Renitec" must not fire on "Renitecish", and more to
    the point a short alias could otherwise match inside an unrelated word and silently
    drag a wrong drug into the query.
    """
    if not aliases:
        return ExpandedQuery(original=text, expanded=text, applied={})

    applied: dict[str, str] = {}
    lowered = text.lower()

    for alias, generic in aliases.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            # Skip when the generic is already there — the clinician wrote both, and
            # repeating it would just weight the embedding toward that one word.
            if re.search(rf"\b{re.escape(generic.lower())}\b", lowered):
                continue
            applied[alias] = generic

    if not applied:
        return ExpandedQuery(original=text, expanded=text, applied={})

    expanded = f"{text} {' '.join(sorted(set(applied.values())))}"
    return ExpandedQuery(original=text, expanded=expanded, applied=applied)
