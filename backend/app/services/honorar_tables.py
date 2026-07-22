"""Turn a HOAI Honorartafel row into a self-describing line — #48, the fee-table case.

The fee tables are the substance of the HOAI, and the runtime guard (table_guard) refuses
every one of their rows on purpose: a bare grid "500 000 34 865 41 530 …" quoted to an
architect states a fee without the Honorarzone it belongs to. That is #48 with money — a
verbatim, correctly-cited number that means something other than it appears to. This makes
the row answerable instead of merely refused, by carrying each zone's heading into the text:

    Anrechenbare Kosten 500 000 Euro — Honorarzone I 34 865 bis 41 530 Euro, Honorarzone II
    41 530 bis 48 195 Euro, … Honorarzone V 61 013 bis 67 679 Euro

**Column structure comes from pdfplumber's extract_tables, not from hand geometry.** The
"Honorarzone II" header is printed on its own line, vertically offset from I and III, so a
row-by-top clustering reads it as a stray line and mis-assigns its column. extract_tables
resolves the columns correctly; the earlier MEC extractor (services/table_extraction.py)
clusters words itself because MEC headers sit on one line, but the HOAI headers do not.

**The one thing geometry hands to a heuristic is the von/bis split within a cell.** The
Mindestsatz and Höchstsatz share a German space thousands-separator ("70 439 85 269"), so
they cannot be separated by spacing. They can be separated by the invariant that binds
them: the Mindestsatz is always below the Höchstsatz. Splitting a cell's digit-groups in
half yields von < bis on every one of the 1 434 fee cells in the official HOAI (measured);
a cell that would not, or that has an odd number of groups, is skipped rather than guessed.
The same "improve only where certain, never guess" rule the MEC extractor keeps — because a
mis-split here is a wrong fee, and a wrong fee is exactly the failure #48 is about.
"""

from __future__ import annotations

import re

# A Honorarzone column header: "Honorarzone III …". The roman numeral is the column's identity.
_ZONE = re.compile(r"Honorarzone\s+([IVX]+)")

# The row-key column: anrechenbare Kosten in Euro, or (for the planning tables) Fläche in Hektar.
_KEY_HEADER = re.compile(r"(Anrechenbare\s+Kosten|Fläche)", re.IGNORECASE)

# A cell line that is only digits and separating spaces — a fee pair or a bare key.
_NUMBER_LINE = re.compile(r"^[\d ]+$")


def _lines(cell: str | None) -> list[str]:
    """A merged table cell split into its stacked sub-rows. extract_tables joins the rows of
    a rule-less table block with newlines, so one 'cell' is really a column of values."""
    if not cell:
        return []
    return [line.strip() for line in cell.split("\n") if line.strip()]


def _to_int(grouped: str) -> int | None:
    digits = grouped.replace(" ", "")
    return int(digits) if digits.isdigit() else None


def von_bis(cell_line: str) -> tuple[str, str] | None:
    """Split "70 439 85 269" into ("70 439", "85 269"), or None if it will not split safely.

    By digit-group count, not spacing — both numbers use the same space separator. Returns
    None unless the halves parse and satisfy 0 < von < bis, which is the guard that turns a
    mis-split into a skipped row rather than a wrong fee.
    """
    tokens = cell_line.split()
    # A pair needs an even, ≥4-token split. "34 865" is two tokens — a *single* number
    # (34 865 = 34865), not a von/bis pair, and splitting it to ("34", "865") would invent a
    # backwards range for a fee that never existed. Every real HOAI fee is ≥ 4 digits, so a
    # genuine pair carries two 2-group numbers = four tokens at minimum; anything shorter is
    # one number and is not a pair. Odd counts are the magnitude-crossing case (von under a
    # thousands boundary, bis over it), which cannot be split without guessing — skipped.
    if len(tokens) < 4 or len(tokens) % 2 != 0:
        return None
    half = len(tokens) // 2
    von_s, bis_s = " ".join(tokens[:half]), " ".join(tokens[half:])
    von, bis = _to_int(von_s), _to_int(bis_s)
    if von is None or bis is None or not 0 < von < bis:
        return None
    return von_s, bis_s


def _key_noun(header_cell: str) -> tuple[str, str]:
    """The row-key's noun and unit, from its header cell: ("Anrechenbare Kosten", "Euro")."""
    noun_match = _KEY_HEADER.search(header_cell or "")
    # collapse the header's internal whitespace: the cell reads "Anrechenbare\nKosten", and
    # \s+ in the pattern matches the newline, so the raw group carries it.
    noun = re.sub(r"\s+", " ", noun_match.group(1)) if noun_match else "Wert"
    unit = "Euro" if "Euro" in (header_cell or "") else "Hektar" if "Hektar" in (header_cell or "") else ""
    return noun, unit


def self_describing_honorar_rows(tables: list[list[list[str | None]]]) -> list[str]:
    """Self-describing fee lines for every mappable Honorartafel row in a page's tables.

    `tables` is pdfplumber `page.extract_tables()` output. Only adds lines; a page whose
    tables are not Honorartafeln yields nothing, so this is safe to run on any document.
    """
    out: list[str] = []
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = table[0]

        key_col: int | None = None
        zones: list[tuple[int, str]] = []
        for index, cell in enumerate(header):
            text = cell or ""
            zone = _ZONE.search(text)
            if zone:
                zones.append((index, f"Honorarzone {zone.group(1)}"))
            elif key_col is None and _KEY_HEADER.search(text):
                key_col = index

        # A Honorartafel needs a key column and at least two zone columns; anything else is a
        # different table and left alone.
        if key_col is None or len(zones) < 2:
            continue

        noun, unit = _key_noun(header[key_col])

        for row in table[1:]:
            keys = _lines(row[key_col])
            if not keys:
                continue
            zone_lines = {index: _lines(row[index]) for index, _ in zones}
            # Each zone column must carry exactly one fee line per key, or the block cannot be
            # aligned without guessing which fee belongs to which anrechenbare-Kosten row.
            if any(len(zone_lines[index]) != len(keys) for index, _ in zones):
                continue

            for i, key in enumerate(keys):
                if not _NUMBER_LINE.match(key):
                    continue
                parts: list[str] = []
                for index, zone_name in zones:
                    pair = von_bis(zone_lines[index][i])
                    if pair is None:
                        break
                    parts.append(f"{zone_name} {pair[0]} bis {pair[1]} Euro")
                if len(parts) == len(zones):
                    prefix = f"{noun} {key} {unit}".strip()
                    out.append(f"{prefix} — " + ", ".join(parts))
    return out
