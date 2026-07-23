"""Refuse a quote that is a category-table row shown without its column heading (#48).

**This is a safety net, not the fix.** #48's fix is a structural table extractor that emits
self-describing rows; that is real work with its own correctness bar (a header-mapping bug
reproduces #48). Until it lands, this stops the dangerous half of #48 from reaching a
clinician: it turns "confidently wrong" into "honestly incomplete".

**What it catches, and why #19 cannot.** `validate_answer` (#19) proves a quote is verbatim
in its chunk — a mechanical substring check with no judgement, deliberately. A quote like

    ii. With aura 1

passes it: the span is there, character for character, and the citation to CDC p102 is
correct. But the `1` is a US MEC category, and the column heading that says *which method* it
applies to — on p102, "barrier methods", where 1 is right and beside the point — is not in the
span. The clinical answer for combined hormonal contraception is category 4. The clinician is
shown a verbatim, correctly-attributed number that means the opposite of what it appears to,
and has nothing to tell the two apart. So this runs *after* #19, in the pipeline, never inside
it — the substring check stays judgement-free.

**Why the shape is a reliable tell, measured (2026-07-17).** A real prose answer spells its
category out inline — "Patients with obesity (BMI >=30 kg/m2) can use implants (U.S. MEC 1)" —
so the number is self-describing. A table row trails a bare category digit with only a *row*
label before it, never the *column* it belongs to. On every answer the system gives today this
refuses exactly the one #48 case and keeps all eleven real prose answers. The tell is a line
ending in one or more bare category cells (a digit 1-4, optionally *, or a dash cell) after a
text label — with the inline "(U.S. MEC N)" forms removed first, and labelled numbers
("Step 2", "Grade 3") excluded, because those name their own scale and need no column heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.answer import AnswerPayload, Citation, NoAnswerReason, SourceGroup

# A bare category cell: a single 1-4 (optionally * for a footnote, or the split N/N form), or
# a dash standing for "no category". The vocabulary of a US MEC table body, nothing else.
_CATEGORY_CELL = re.compile(r"^(?:[1-4]\*?|[1-4]/[1-4]\*?|[—–-])$")

# Inline, self-describing category phrases. Removed before the scan so that a prose sentence
# ending "(U.S. MEC 1)" is not mistaken for a table row ending in a bare 1.
_INLINE_CATEGORY = re.compile(r"\(\s*U\.S\.\s*MEC\s*[1-4]\s*\)|\(\s*[1-4]\s*\)", re.IGNORECASE)

# A number that names its own scale needs no column heading: "Step 2", "Grade 3", "Class II".
# When the token before the trailing digits is one of these, the digit is self-describing and
# the line is not a headerless row. Kept small and structural, not a medical dictionary.
_SELF_LABELLING = frozenset(
    {
        "step", "steps", "level", "class", "grade", "stage", "phase", "type",
        "category", "week", "weeks", "day", "days", "year", "years", "age",
        "figure", "table", "box", "section", "chapter", "no", "no.", "number",
        "item", "day(s)", "month", "months",
    }
)

# A label must have this much text before the digits to count as a table row's row-label,
# rather than a stray fragment. Four characters clears "a." and "1a" without clearing "aura".
_MIN_LABEL_CHARS = 4


# A numeric grid row: a line that is nothing but numbers. HOAI's Honorartafeln are pages of
# these — "500 000 34 865 41 530 41 530 48 195 ..." — where the leading number is the
# anrechenbare Kosten and each following pair is a Honorarzone's von/bis fee. The column
# headings live in a separate header row that no chunk carries with the body.
#
# The rule above cannot see these. It was built for US MEC tables and asks for a *text* row
# label followed by category cells 1-4; here the row label is itself a number and the cells
# are five-digit euro amounts, so it scores run=0 and passes the line through. Read against
# the PDF, that is a quote which tells an architect a fee without saying which Honorarzone it
# belongs to — the same failure as #48's contraception category, in the corpus where the
# tables *are* the content.
#
# Structural, not a euro-detector: a line of six or more bare numbers and nothing else is a
# grid row in any document. Real prose does not produce that — a sentence with numbers in it
# has words between them, and a short "1 2 3" list stays under the threshold. Thousands
# separators are spaces in German typesetting, so "500 000" is two tokens and the threshold
# is counted in tokens deliberately: it makes the test *harder* to trip, not easier.
_NUMERIC_TOKEN = re.compile(r"^[+-]?[\d.,]+$")
_MIN_GRID_NUMBERS = 6


def _is_numeric_grid_row(tokens: list[str]) -> bool:
    return len(tokens) >= _MIN_GRID_NUMBERS and all(_NUMERIC_TOKEN.match(t) for t in tokens)


def looks_like_headerless_table_row(quote: str) -> bool:
    """Whether the quote contains a line that is a category-table row missing its heading.

    Judged per physical line, because a table row is exactly "row label ... trailing category
    cells" and a quote may carry several. See the module docstring for the reasoning and the
    measurement behind each rule.
    """
    for line in quote.splitlines():
        tokens = _INLINE_CATEGORY.sub("", line).split()
        if not tokens:
            continue

        # Checked before the category-cell scan, because a fee row has no text label for
        # that scan to find and would otherwise fall through to `continue`.
        if _is_numeric_grid_row(tokens):
            return True

        run = 0
        for token in reversed(tokens):
            if _CATEGORY_CELL.match(token):
                run += 1
            else:
                break
        if run == 0:
            continue

        label_tokens = tokens[: len(tokens) - run]
        label = " ".join(label_tokens)
        if not re.search(r"[A-Za-z]", label) or len(label) < _MIN_LABEL_CHARS:
            # No row label, so this is a fragment of digits, not a labelled row.
            continue
        if label_tokens and label_tokens[-1].lower() in _SELF_LABELLING:
            # "Step 2", "Grade 3" — the number names its own scale, no heading needed.
            continue
        if label_tokens and label_tokens[-1].endswith(":"):
            # "CHC: 4" — a key:value pair names its own column, so the number is not
            # headerless. This is exactly the self-describing form services/table_extraction.py
            # emits from a mapped table row (#48): the fix's output must pass the guard, or the
            # guard would refuse the very rows the extractor made safe to show.
            continue
        return True
    return False


@dataclass(frozen=True)
class DroppedRow:
    """A citation refused because it is a headerless table row (#48), not a fabrication."""

    chunk_id: object
    quote: str


@dataclass(frozen=True)
class GuardResult:
    payload: AnswerPayload
    dropped: list[DroppedRow] = field(default_factory=list)


def guard_table_rows(payload: AnswerPayload) -> GuardResult:
    """Drop every citation that is a headerless category-table row, per citation.

    Mirrors validate_answer: per citation, not all-or-nothing, so a real prose quote is never
    discarded over a table-row neighbour. When the guard empties the answer, the reason is
    TABLE_NOT_CITABLE — the corpus answers, we cannot cite the table safely yet — not
    SOURCES_DO_NOT_ANSWER, which would tell the clinician the guidance is absent.
    """
    dropped: list[DroppedRow] = []
    surviving_groups: list[SourceGroup] = []

    for group in payload.groups:
        kept: list[Citation] = []
        for citation in group.citations:
            if looks_like_headerless_table_row(citation.quote):
                dropped.append(DroppedRow(chunk_id=citation.chunk_id, quote=citation.quote))
            else:
                kept.append(citation)
        if kept:
            surviving_groups.append(group.model_copy(update={"citations": kept}))

    if not dropped:
        return GuardResult(payload=payload, dropped=[])

    reason = payload.no_answer_reason
    if not surviving_groups and reason is None:
        reason = NoAnswerReason.TABLE_NOT_CITABLE

    # Rebuilt, not model_copy'd, for the same reason validate_answer rebuilds: an empty answer
    # must carry a reason, and model_copy would skip the invariant that enforces it. And from
    # the payload's own fields, overriding only what this step CHANGES — see validate_answer:
    # listing what to preserve is what quietly drops the next field added upstream.
    guarded = AnswerPayload(
        **{
            **payload.model_dump(),
            "groups": surviving_groups,
            "conflicts": payload.conflicts if surviving_groups else [],
            "no_answer_reason": reason,
        }
    )
    return GuardResult(payload=guarded, dropped=dropped)
