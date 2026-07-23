"""Turn a US MEC category-table row into a self-describing line (#48).

This is the real fix the runtime guard (services/table_guard.py) only stood in for. #48's
danger is a row quoted without its column heading: "ii. With aura 1" is category 1 on p102's
barrier table, but the combined-hormonal answer is category 4, and the number that says so sits
in a column whose header — the method name — is not in the quoted span. The guard makes that
fail safe by refusing the row. This makes it *answerable*, by carrying the header into the text
so the row is self-describing:

    Migraine with aura — Cu-IUD: 1, LNG-IUD: 1, Implant: 1, DMPA: 1, POP: 1, CHC: 4*

A chunk containing that line retrieves on the natural-language question and can be quoted
verbatim without losing what each number applies to. CHC: 4 — the stroke contraindication — is
finally sayable.

**Safe by construction, on both axes the session cares about.** It transforms a row ONLY when
the mapping is unambiguous: the count of category cells equals the count of method headers, and
every cell sits within tolerance of its header's column centre. The p102 barrier table (three
named methods, four value columns) fails that check and is left exactly as it is — so a
header-mapping guess can never reproduce #48, and an unclear table is never dropped, it simply
falls back to the current behaviour the guard already handles. Improve where certain, never
guess.

**Column geometry, measured on p124.** The six method headers sit at x ≈ 176/249/321/394/467/
540, ~73pt apart, and the aura row's six cells align to them exactly (Δ0). Tolerance is a third
of that spacing: comfortably inside a column, nowhere near its neighbour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The method columns a US MEC eligibility table can carry, by their printed abbreviations.
# A header row is recognised by holding several of these; a stray "POP" in prose is not a table.
KNOWN_METHODS = (
    "Cu-IUD",
    "LNG-IUD",
    "Implant",
    "DMPA",
    "POP",
    "CHC",
    "COC",
    "CIC",
    "P",  # progestin-only pill, in some tables
)

# A category cell: 1-4, optionally a footnote star or the split "N/N" form, or a dash for
# "no category". The vocabulary of a table body — the same shape table_guard refuses.
_CATEGORY_CELL = re.compile(r"^(?:[1-4]\*?|[1-4]/[1-4]\*?|[—–-])$")

# At least this many method headers must appear on a line for it to be a table header. Three
# separates a real eligibility table from a sentence that happens to name a method.
_MIN_METHODS = 3

# A cell must sit within this many points of a header's centre to map to it. A third of the
# ~73pt column spacing measured on p124 — inside the column, clear of its neighbours.
_ALIGN_TOLERANCE = 24.0


def _centre(word: dict) -> float:
    return (word["x0"] + word["x1"]) / 2


@dataclass(frozen=True)
class MethodColumn:
    header: str
    x: float


def find_method_columns(header_words: list[dict]) -> list[MethodColumn] | None:
    """The method columns of a US MEC table, from the words on its header line.

    `header_words` are the words of one line (each a dict with text/x0/x1). Returns the columns
    left-to-right, or None when the line is not a method-table header — fewer than _MIN_METHODS
    abbreviations, i.e. prose that merely mentions a method.
    """
    columns = [
        MethodColumn(header=w["text"], x=_centre(w))
        for w in header_words
        if w["text"] in KNOWN_METHODS
    ]
    if len(columns) < _MIN_METHODS:
        return None
    return sorted(columns, key=lambda c: c.x)


def map_row(row_words: list[dict], columns: list[MethodColumn]) -> list[tuple[str, str]] | None:
    """Map a data row's category cells to their method columns, or None if not certain.

    None — the fall-back — whenever the row cannot be mapped without guessing: a different
    number of cells than columns, or a cell that does not sit within tolerance of any column's
    centre. That is the whole safety property: an ambiguous row (p102's three-method/four-column
    barrier table) returns None and is left to the guard, never mis-attributed.
    """
    cells = sorted(
        (w for w in row_words if _CATEGORY_CELL.match(w["text"])),
        key=lambda w: w["x0"],
    )
    if len(cells) != len(columns):
        return None

    mapping: list[tuple[str, str]] = []
    for cell, column in zip(cells, columns, strict=True):
        if abs(_centre(cell) - column.x) > _ALIGN_TOLERANCE:
            return None
        mapping.append((column.header, cell["text"]))
    return mapping


def describe_row(label: str, mapping: list[tuple[str, str]]) -> str:
    """A self-describing line: the row label, then each method with its category.

    "Migraine with aura — Cu-IUD: 1, LNG-IUD: 1, Implant: 1, DMPA: 1, POP: 1, CHC: 4*"
    """
    cells = ", ".join(f"{header}: {value}" for header, value in mapping)
    return f"{label.strip()} — {cells}"


_ROW_TOLERANCE = 4.0  # words within this many points of top share a row

# A label that opens with an enumerator — "i.", "ii.", "a)", "3." — is a sub-item of the
# heading above it. In the MEC summary table the condition sits on its own line ("b. Migraine")
# and its sub-rows ("i. Without aura", "ii. With aura") carry only the sub-label — the word a
# clinician actually searches for, "migraine", is on the parent line and the raw sub-row drops
# it. Measured consequence: the self-describing "ii. With aura — …, CHC: 4*" row embeds ~0.16
# from "migraine with aura", one rank below the passage window, while the raw sub-row (which
# still says "Migraine") gets quoted and then refused by the guard — so the #48 answer exists
# in the corpus and never reaches anyone. Carrying the parent condition into the label puts the
# searched word back and lifts the safe row into the window.
_SUB_ENUMERATOR = re.compile(r"^(?:[ivxlcdm]+|[a-z]|\d{1,2})[.)](?:\s+|$)", re.IGNORECASE)


def _strip_enumerator(label: str) -> str:
    """Drop a leading "ii. " / "b. " so a carried parent reads as the condition, not its bullet."""
    return _SUB_ENUMERATOR.sub("", label, count=1).strip()


def _row_label(row: list[dict], columns: list[MethodColumn]) -> str:
    """The text left of the first value column — a row's label, header, or nothing."""
    first_col = columns[0].x
    label_words = [w for w in row if _centre(w) < first_col - _ALIGN_TOLERANCE]
    return " ".join(w["text"] for w in sorted(label_words, key=lambda w: w["x0"]))


# A label that stops mid-phrase. Either signal means the row below carries the rest of it.
_DANGLING_TAIL = re.compile(
    r"(?:\b(?:e\.g\.|i\.e\.|and|or|the|of|with|for|in|to)|,)$", re.IGNORECASE
)


def _looks_truncated(label: str) -> bool:
    """Whether a label was cut where the printed cell wrapped to a second line.

    `_row_label` reads one geometric row, so a cell that wraps loses its tail: measured on the
    real MEC, 32 labels carry an unclosed bracket and 59 end on a word no phrase ends on —
    "Thrombophilia (e.g.,", "d. Family history (first-degree", and, worst, "ii. Systolic ≥160
    mm Hg or", which drops the diastolic half of a blood-pressure threshold.
    """
    stripped = label.strip()
    if not stripped:
        return False
    return stripped.count("(") != stripped.count(")") or bool(_DANGLING_TAIL.search(stripped))


# How many wrapped lines a label may absorb. The cap exists so a label that never reads
# complete cannot swallow the rest of the table.
#
# Five, measured rather than guessed. At three, eleven labels stayed cut; raising it resolves
# four more and then plateaus (11 → 9 → 7 → 7 at caps 3/4/5/6). Every line the extra budget
# absorbs was read against the source and is a genuine continuation of the same phrase —
# "…previous VTE, thrombophilia, immobility," + "transfusion at delivery, peripartum", and
# "…or history of subacute bacterial" + "endocarditis)", which closes its own bracket. No
# sibling row and no prose is pulled in, because the loop still stops at any row carrying
# category cells and the moment the label reads complete.
#
# A crude "did a label swallow another row's text" metric reported 26-31 hits at every cap
# INCLUDING the unchanged one, which is how it was identified as noise: condition names are
# routinely substrings of each other ("Migraine" inside "Migraine with aura"). Reading the six
# distinct diffs was what actually settled it.
_MAX_CONTINUATION_ROWS = 5


def _complete_label(rows: list[list[dict]], index: int, columns: list[MethodColumn]) -> str:
    """The row's label, extended across the lines its printed cell wrapped onto.

    **Only extends a label that already looks truncated, and stops the moment it reads
    complete.** That is what keeps this safe: the several hundred labels that were never cut
    are not touched at all, so the fix cannot regress them — it can only act where a detector
    already says the text is broken.

    A continuation row is recognised by what it is not: it carries no category cells (that
    would be the next data row) and it has words in the label region (the comment column sits
    to the right of the values and is excluded by _row_label's geometry). Capitalisation is no
    help here and is deliberately not used — "BMI ≥30 kg/m2" and "VTE (e.g., …" are both real
    continuations that begin with a capital, exactly like a heading would.
    """
    label = _row_label(rows[index], columns)
    if not _looks_truncated(label):
        return label

    for offset in range(1, _MAX_CONTINUATION_ROWS + 1):
        following = index + offset
        if following >= len(rows):
            break
        row = rows[following]
        if any(_CATEGORY_CELL.match(word["text"]) for word in row):
            break  # the next data row: the label ended, however it reads
        tail = _row_label(row, columns)
        if not tail:
            break
        label = f"{label} {tail}".strip()
        if not _looks_truncated(label):
            break
    return label


def _rows(words: list[dict]) -> list[list[dict]]:
    """Group a page's words into rows by their vertical position, top to bottom."""
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(word["top"] - rows[-1][0]["top"]) <= _ROW_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])
    return rows


def self_describing_lines(words: list[dict]) -> list[str]:
    """Self-describing lines for every mappable category-table row on a page (#48).

    Given a page's words (each with text/x0/x1/top), find each method-header row and, for the
    data rows beneath it, emit "<row label> — Method: category, ..." — but only for rows that
    map without ambiguity (see map_row). Rows that do not map are silently skipped: this only
    ADDS answerable text, it never replaces or removes what extraction already produced, so a
    page with no clean table simply yields nothing and nothing is at risk.
    """
    rows = _rows(words)
    out: list[str] = []
    columns: list[MethodColumn] | None = None
    # The condition heading whose sub-rows we are currently under — "Migraine" for the rows
    # beneath "b. Migraine". None when not inside such a group. See _SUB_ENUMERATOR.
    parent: str | None = None
    for index, row in enumerate(rows):
        header = find_method_columns(row)
        if header is not None:
            columns = header
            parent = None  # a new table; nothing above it is a parent
            continue
        if columns is None:
            continue

        # Indexed, because a label whose printed cell wrapped is finished on the rows below
        # it — see _complete_label. A row that is not truncated reads exactly as before.
        label = _complete_label(rows, index, columns)
        mapping = map_row(row, columns)

        if mapping is None:
            # `mapping is None` covers two unrelated rows, and conflating them is what put 13
            # mislabelled conditions into the corpus:
            #
            #   "b. Migraine"              a real heading — no category cells at all
            #   "a. Uncomplicated  1 1 …"  a *sibling data row* whose cells map_row refused
            #                              (7 cells against 6 columns, misaligned, …)
            #
            # Both reach here, and treating the second as a heading made the next sub-row read
            # "Uncomplicated — Complicated (pulmonary …" — siblings presented as parent and
            # child. Measured on the real MEC: "Compensated (normal liver — Decompensated
            # (impaired", "<6 months — ≥6 months". The categories stay right and the condition
            # name goes wrong, which is #48 exactly: verbatim, correctly cited, and read as
            # something it is not.
            #
            # So a heading is a row carrying NO category cells. A row that has them is a
            # sibling whose mapping was refused — never a parent.
            has_cells = any(_CATEGORY_CELL.match(word["text"]) for word in row)
            if label and _SUB_ENUMERATOR.match(label) and not has_cells:
                parent = _strip_enumerator(label)
            elif label[:1].isupper() and not has_cells:
                parent = None
            # A refused sibling neither sets nor clears: the parent above it still governs the
            # next sibling, which is what keeps "Migraine — With aura" intact when the
            # without-aura row beside it happens to fail its own mapping.
            continue

        if not re.search(r"[A-Za-z]", label):
            continue

        full_label = label
        if parent and _SUB_ENUMERATOR.match(label):
            full_label = f"{parent} — {_strip_enumerator(label)}"
        out.append(describe_row(full_label, mapping))

        # A data row whose own label is top-level (no enumerator) ends the sub-group, so its
        # parent must not leak onto the next condition's sub-rows.
        if not _SUB_ENUMERATOR.match(label):
            parent = None
    return out
