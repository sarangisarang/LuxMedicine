"""Recognise a bibliography chunk, so it never sources a clinical answer (#50).

A reference list is not guidance. But its entries are dense with clinical vocabulary — paper
titles say "Combined hormonal contraceptive use among women with..." — so they match a query
lexically and by embedding, and out-rank the guidance that actually answers it. Measured on the
active corpus (2026-07-17): NHLBI EPR-3 is 34-38% bibliography, and for
`contraception-postpartum` nine of the top eleven retrieved chunks were citations.

**Recognised by the shape of a citation line, not its topic.** A reference line carries an
author-initial run ("Tepper NK,"), a journal/volume or DOI ("2008;77", "10.1016/..."), or an
"et al". Guidance prose does not. A chunk is a reference when most of its lines are that shape.

**The bar this is built to, measured before it was wired in.** Precision is what matters:
flagging a guidance chunk would hide an answer, the invisible failure this project fears most.
Of 560 CDC chunks this flags, exactly one carries any guidance marker (Comment/Clarification/
"can use"/"U.S. MEC"/Category) — and that one is a paper title containing the word "should", a
genuine reference. So it does not hide guidance. Recall is looser and that is the safe
direction: a missed citation leaves some pollution, where a wrongly-flagged guidance chunk
removes an answer. The definitive gate is the eval — excluding these must regress no answering
question.
"""

from __future__ import annotations

import re

# An entry number opening a citation: "91. Tepper", "181 Berry-Bibee".
_ENTRY = re.compile(r"^\s*\d{1,3}\.?\s+[A-Z]")

# An author-initial run: "Tepper NK," / "Kim MJ." / "Curtis KM; " / "Gaffield ME et al".
_AUTHORS = re.compile(r"[A-Z][a-z]+\s+[A-Z]{1,3}(?:,|\.|;| et al)")

# A journal/volume, DOI, URL, or "et al" — the machinery of a citation, never of guidance.
_JOURNAL = re.compile(r"(?:19|20)\d{2};\d|\bdoi\b|https?://|10\.\d{4}/|\bet al\b", re.IGNORECASE)

# A line is bibliographic if it carries any of those signals.
def _is_reference_line(line: str) -> bool:
    return bool(
        _AUTHORS.search(line)
        or _JOURNAL.search(line)
        or (_ENTRY.match(line) and _AUTHORS.search(line))
    )


# Most of a reference chunk's lines are citations. 0.55 sits above the density of a guidance
# chunk that merely ends with a citation, and below a genuine list — measured, not guessed.
_REFERENCE_LINE_FRACTION = 0.55


def looks_like_reference(content: str) -> bool:
    """Whether this chunk is a bibliography entry rather than guidance (#50)."""
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return False
    hits = sum(1 for line in lines if _is_reference_line(line))
    return hits / len(lines) >= _REFERENCE_LINE_FRACTION
