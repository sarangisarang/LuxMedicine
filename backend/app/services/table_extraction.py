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
    for row in rows:
        header = find_method_columns(row)
        if header is not None:
            columns = header
            continue
        if columns is None:
            continue
        mapping = map_row(row, columns)
        if mapping is None:
            continue
        # The row label is the text left of the first value column — the Condition cell.
        first_col = columns[0].x
        label_words = [w for w in row if (w["x0"] + w["x1"]) / 2 < first_col - _ALIGN_TOLERANCE]
        label = " ".join(w["text"] for w in sorted(label_words, key=lambda w: w["x0"]))
        if not re.search(r"[A-Za-z]", label):
            continue
        out.append(describe_row(label, mapping))
    return out
