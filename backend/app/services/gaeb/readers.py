"""Readers: any source file → the neutral `BillOfQuantities`.

Each format is a small function that ends in the same model, so the writer never learns where a
position came from. The two reliable, zero-guess sources come first — an existing GAEB file (already
structured) and a spreadsheet (columns are explicit) — because those are what a bidder actually keeps
an LV in. A PDF reader is a later stage: extracting a table out of a page is the fragile part, and it
belongs behind the same human-verify step, not silently trusted.

Numbers are the quiet hazard. A German LV writes "1.234,56" (dot thousands, comma decimal); an
exported one may write "1234.56". `parse_decimal` reads both, and everything is `Decimal` — a bid
total summed as float would drift the last cent.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from decimal import Decimal, InvalidOperation

import docx
import openpyxl
import pdfplumber

from app.services.gaeb.model import BillOfQuantities, Entry, Position


class UnreadableFileError(ValueError):
    """The file could not be parsed as the format it claimed (corrupt, empty, or wrong type)."""


class UnsupportedFormatError(ValueError):
    """A format we recognise but do not yet convert (PDF, scanned images) — a clear 'not yet',
    distinct from a file we failed to read."""


# GAEB DA XML exchange-phase extensions. All are the same XML dialect; only the phase differs.
_GAEB_EXTS = frozenset({"x80", "x81", "x82", "x83", "x84", "x85", "x86", "x89", "xml", "gaeb"})


class NoPositionsError(ValueError):
    """The file parsed, but no bill-of-quantities positions could be found in it — usually a
    spreadsheet whose columns were not recognised."""


# Unambiguous shapes that reveal which convention a file is written in. "1.234,56" and "2,57" can
# only be German; "1,234.56" and "2.57" can only be English.
_GERMAN_NUMBER = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d+$|^\d+,\d{1,2}$")
_ENGLISH_NUMBER = re.compile(r"^\d{1,3}(?:,\d{3})+\.\d+$|^\d+\.\d{1,2}$")


def detect_thousands_separator(cells: list[str]) -> str | None:
    """Which character this file uses to group thousands, judged from the values it contains.

    Needed because a lone separator with exactly three digits after it is genuinely ambiguous:
    "1.180" is one thousand one hundred and eighty in a German LV and one-point-one-eight in an
    English one, and guessing wrong changes a quantity by a factor of a thousand — silently, since
    both readings are valid numbers. So rather than assume, look at the whole file: "2,57" and
    "1.234,56" can only be German, "2.57" and "1,234.56" only English, and the weight of that
    evidence decides the ambiguous cases.
    """
    german = english = 0
    for cell in cells:
        text = (cell or "").strip()
        if not text:
            continue
        if _GERMAN_NUMBER.match(text):
            german += 1
        elif _ENGLISH_NUMBER.match(text):
            english += 1
    if german > english:
        return "."
    if english > german:
        return ","
    return None


def parse_decimal(raw: str | None, *, thousands: str | None = None) -> Decimal | None:
    """Read a quantity or price written in either German or English convention. Returns None for a
    blank cell (an unpriced position is valid in the model; the writer decides if that blocks .x84).

    `thousands` resolves the one genuinely ambiguous shape — a single separator followed by exactly
    three digits — with the convention detected for the file as a whole. Without it such a value is
    read as a decimal point, which is the safer reading of a lone number but wrong for a German LV.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    for junk in (" ", " ", "€", "EUR", "	"):
        s = s.replace(junk, "")
    if not s:
        return None

    if "," in s and "." in s:
        # Both present: whichever comes last is the decimal point, the other groups thousands.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # 1.234,56 -> 1234.56
        else:
            s = s.replace(",", "")  # 1,234.56 -> 1234.56
    else:
        for sep in (".", ","):
            if sep not in s:
                continue
            head, _, tail = s.rpartition(sep)
            repeated = s.count(sep) > 1
            grouped = len(tail) == 3 and tail.isdigit() and bool(head)
            if repeated or (grouped and thousands == sep):
                s = s.replace(sep, "")  # 1.234.567, or 1.180 -> 1180 in a German file
            elif sep == ",":
                s = s.replace(",", ".")  # 1234,56 -> 1234.56
            break

    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise UnreadableFileError(f"not a number: {raw!r}") from exc


# Canonical field -> the header labels that mean it (lower-cased, German first). A column matches if
# its header equals or starts with one of these. Real LVs label these columns a dozen ways, so the
# list is deliberately long — and when none of it matches, `_infer_columns_by_content` takes over.
_HEADERS: dict[str, tuple[str, ...]] = {
    "oz": (
        "oz", "o.z", "ordnungszahl", "positionsnummer", "positions-nr", "pos.-nr", "pos-nr",
        "pos.nr", "position", "pos", "nummer", "nr", "lfd", "item", "no.", "no",
        # A Kostenberechnung heads this column with both meanings at once, because it holds cost
        # group numbers and position numbers in the same column.
        "kg / oz", "kg/oz", "kg oz", "oz / kg", "kg",
    ),
    "short_text": (
        "kurztext", "bezeichnung", "beschreibung", "leistungsbeschreibung", "leistung",
        "leistungstext", "positionstext", "artikel", "benennung", "text", "description",
        "title", "gegenstand",
        # "DIN 276 (2018-12) / Quelleinträge" — the column that carries group labels and
        # Leistungstexte together.
        "din 276", "din276", "din", "quelleinträge", "quelleintrag", "quelleintraege",
    ),
    "quantity": (
        "menge", "mengenansatz", "vordersatz", "anzahl", "stück", "stueck", "qty", "quantity",
        "masse", "umfang",
    ),
    "unit": (
        "einheit", "mengeneinheit", "mengen-einheit", "me", "m.e", "eh", "unit", "qu", "einh",
    ),
    "unit_price": (
        "einheitspreis", "einheits-preis", "einzelpreis", "e-preis", "ep", "up", "preis/einheit",
        "preis", "unitprice", "unit price", "netto", "angebotspreis",
    ),
    # A partial amount printed on the line, distinct from the line total.
    "teilbetrag": ("teilbetrag", "teil-betrag", "teilsumme", "anteil", "partial"),
    "long_text": ("langtext", "detailtext", "spezifikation", "long text", "zusatztext"),
    "section": ("titel", "los", "gruppe", "gewerk", "abschnitt", "section", "kapitel"),
    # Recognised so it can be kept OUT of unit_price. A .x84 carries the unit price and derives the
    # line total itself; exporting a total as if it were a unit price multiplies the bid by the
    # quantity, so this column is identified precisely in order to be ignored.
    "total": (
        "gesamtpreis", "gesamtbetrag", "gesamtsumme", "gesamt", "gp", "g-preis", "summe",
        "betrag", "endpreis", "total", "line total", "positionssumme",
    ),
}

# Words that mean "this is a sum, not a rate". A label containing one of these can never be the unit
# price, however it otherwise reads — "Preis gesamt" starts with "preis" and is emphatically a total.
_TOTAL_WORDS = ("gesamt", "summe", "brutto", "total", "betrag", "endpreis")

# The quantity units a German LV actually uses. Used only by the content-based fallback: a column
# whose cells are mostly these IS the unit column, whatever its header says (or if it has none).
_UNIT_TOKENS = frozenset({
    "m", "m2", "m²", "m3", "m³", "qm", "cbm", "lfm", "lfdm", "mm", "cm", "km",
    "st", "stk", "stck", "stück", "stueck", "psch", "pausch", "pa", "kg", "to", "t", "g",
    "h", "std", "stunde", "stunden", "l", "ltr", "liter", "st.", "we", "ea", "pcs", "set",
})


def _map_columns(header: list[str]) -> dict[str, int]:
    """Match each header cell to a canonical field. First column wins a field, so a stray later
    'Position' does not steal it from the real one.

    A cell is matched to the field whose LONGEST recognised label it starts with, not to whichever
    field happens to be checked first: "Gesamtpreis" must land on `total` (11 characters of evidence)
    rather than on `unit_price` via a short prefix, because reading a line total as a unit price
    multiplies the whole bid by the quantity.
    """
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        label = (cell or "").strip().lower()
        if not label:
            continue
        best_field, best_len = None, 0
        for field, names in _HEADERS.items():
            if field in mapping:
                continue
            if field == "unit_price" and any(word in label for word in _TOTAL_WORDS):
                continue  # a sum, never a rate — see _TOTAL_WORDS
            for name in names:
                if (label == name or label.startswith(name)) and len(name) > best_len:
                    best_field, best_len = field, len(name)
        if best_field is not None:
            mapping[best_field] = idx
    return mapping


def _maybe_decimal(raw: str | None, *, thousands: str | None = None) -> Decimal | None:
    """`parse_decimal`, but a value that is not a number reads as absent rather than as an error.

    Right for reading a FILE: a grid contains header cells, section titles, notes and totals mixed in
    with the positions, and "this cell is not a number" is how we recognise a row that is not a
    position — not a reason to reject the whole document. Kept separate from `parse_decimal`, which
    stays strict for values a person typed into the verify table, where a typo must be reported.
    """
    try:
        return parse_decimal(raw, thousands=thousands)
    except UnreadableFileError:
        return None


def parse_quantity(cell: str | None, thousands: str | None = None) -> tuple[Decimal | None, str]:
    """Read a quantity cell into its number and whatever unit was printed with it.

    One reader for every caller — the file, and the table sent back for export — because they must
    agree: a cell that is a valid quantity on the way in cannot be rejected as "not a number" on the
    way out. The forms a real LV produces, in order of confidence:
      "25"        the number alone
      "720 m2"    number and unit, separated, unit recognised
      "25m3"      run together, no space at all
      "1Ps..."    the unit cut short by the source's own column width
    This is the quantity column, so a cell that begins with a number IS a quantity, whatever follows.
    """
    text = (cell or "").strip()
    if not text:
        return None, ""

    direct = _maybe_decimal(text, thousands=thousands)
    if direct is not None:
        return direct, ""

    number, unit = split_quantity_unit(text)
    if unit:
        value = _maybe_decimal(number, thousands=thousands)
        if value is not None:
            return value, unit

    leading = re.match(r"^([\d.,]+)\s*(.*)$", text)
    if leading:
        value = _maybe_decimal(leading.group(1), thousands=thousands)
        if value is not None:
            return value, leading.group(2).strip().rstrip(".")
    return None, ""


def _numeric_value(value: str | None, thousands: str | None = None) -> Decimal | None:
    """The number in a cell, including one that carries its unit ("720 m2" -> 720).

    Every place that judges a column — is it numeric? does quantity × price equal the total? — must
    read a cell the same way the position parser does, or a quantity written as "132 St" looks like
    no number at all and the check silently passes on nothing.
    """
    direct = _maybe_decimal(value, thousands=thousands)
    if direct is not None or not value:
        return direct
    number, unit = split_quantity_unit(value)
    return _maybe_decimal(number, thousands=thousands) if unit else None


def _is_numeric(value: str, thousands: str | None = None) -> bool:
    return _numeric_value(value, thousands) is not None


def _all_cells(rows: list[list[str]]) -> list[str]:
    return [c for row in rows for c in row]


# "720 m2", "25 Stk", "1.180 m" — a quantity and its unit printed in one column, which is how a PDF
# or Word LV usually sets them. Read as a single value it is not a number at all and the whole row
# would be dropped, so the two are separated here.
# The separator must be whitespace, and the trailing token is checked against the unit vocabulary
# below — so "720 m2" splits (a unit may contain digits, as m2 and m3 do) while "1.180" or "12x4"
# never does.
_QTY_WITH_UNIT = re.compile(r"^([\d.,]+)\s+(\S{1,6})$")


def split_quantity_unit(text: str) -> tuple[str, str]:
    """Split "720 m2" into ("720", "m2"). Only when the trailing token really is a unit — otherwise
    the text is returned untouched, so "1.180" or "12a" is never silently truncated."""
    match = _QTY_WITH_UNIT.match((text or "").strip())
    if not match:
        return text, ""
    number, suffix = match.group(1), match.group(2)
    if suffix.lower().rstrip(".") not in _UNIT_TOKENS:
        return text, ""
    return number, suffix


# "01.0010", "1.1", "1.2.30", "1-2" — an Ordnungszahl, not a measurement. It parses as a number,
# which is the trap: read as a quantity it shifts every following column by one.
_POSITION_NUMBER = re.compile(r"^\d+(?:[.\-/]\d+)+$")


def _looks_like_position_numbers(cells: list[str]) -> bool:
    filled = [c for c in cells if c]
    if not filled:
        return False
    return sum(1 for c in filled if _POSITION_NUMBER.match(c)) / len(filled) >= 0.6


def _multiplies_out(
    rows: list[list[str]], qty: int, unit_price: int, total: int, thousands: str | None = None
) -> bool:
    """True when quantity × unit_price equals the total column on most rows where all three are
    present. This is what tells a unit price from a line total when both are just numbers — the
    arithmetic is the evidence, rather than a guess from column order."""
    checked = 0
    agreed = 0
    for row in rows:
        values = []
        for idx in (qty, unit_price, total):
            values.append(_numeric_value(row[idx], thousands) if idx < len(row) else None)
        q, u, t = values
        if q is None or u is None or t is None or q == 0 or u == 0:
            continue
        checked += 1
        # A cent of slack: the file's own total is rounded, and so is ours.
        if abs(q * u - t) <= Decimal("0.02"):
            agreed += 1
    return checked >= 1 and agreed / checked >= 0.8


def _infer_columns_by_content(rows: list[list[str]]) -> dict[str, int]:
    """Work out which column is which by looking at the DATA, for the files whose headers we do not
    recognise (or that have none at all — plenty of PDF and Word LVs print no header row).

    The signals are the ones a person uses at a glance: the unit column is the one full of "m2"/"St"/
    "psch"; quantities are the numbers next to it; the price is the other number column, to its right;
    the description is the column with the long prose. Only ever a fallback — an explicit header wins,
    and whatever this infers still lands in the human-verify table before anything is exported.
    """
    if not rows:
        return {}
    width = max(len(r) for r in rows)
    thousands = detect_thousands_separator(_all_cells(rows))

    def column(idx: int) -> list[str]:
        return [(r[idx].strip() if idx < len(r) else "") for r in rows]

    stats = []
    for idx in range(width):
        cells = [c for c in column(idx) if c]
        if not cells:
            stats.append(
                {"idx": idx, "filled": 0, "numeric": 0.0, "unit": 0.0, "avg_len": 0.0,
                 "position_like": False}
            )
            continue
        numeric = sum(1 for c in cells if _is_numeric(c, thousands)) / len(cells)
        unitish = sum(1 for c in cells if c.lower().rstrip(".") in _UNIT_TOKENS) / len(cells)
        avg_len = sum(len(c) for c in cells) / len(cells)
        stats.append(
            {"idx": idx, "filled": len(cells), "numeric": numeric, "unit": unitish,
             "avg_len": avg_len, "position_like": _looks_like_position_numbers(cells)}
        )

    mapping: dict[str, int] = {}

    # 1. The unit column is unmistakable: mostly tokens from the unit vocabulary.
    units = [s for s in stats if s["unit"] >= 0.5 and s["filled"] > 0]
    if units:
        mapping["unit"] = max(units, key=lambda s: (s["unit"], s["filled"]))["idx"]

    # 2. Quantities are numeric — but a position number ("01.0010", "1.1") also parses as a number,
    #    and mistaking that column for the quantity shifts EVERY later column by one, which is how a
    #    unit price silently becomes something else. So position-shaped columns are excluded here and
    #    claimed as the OZ instead.
    numeric_cols = [
        s for s in stats if s["numeric"] >= 0.6 and s["filled"] > 0 and not s["position_like"]
    ]
    if numeric_cols:
        if "unit" in mapping:
            left = [s for s in numeric_cols if s["idx"] < mapping["unit"]]
            mapping["quantity"] = (left[-1] if left else numeric_cols[0])["idx"]
        else:
            mapping["quantity"] = numeric_cols[0]["idx"]

    # 3. The unit price. A priced LV usually carries BOTH a unit price and a line total, and taking
    #    the wrong one is a money error, so this is decided by arithmetic rather than by position:
    #    the unit price is the column u for which quantity × u equals some other column t (the total).
    #    Only if no pair adds up does it fall back to "the first number after the quantity".
    after = [s for s in numeric_cols if s["idx"] > mapping.get("quantity", -1)]
    if after:
        qty_idx = mapping.get("quantity")
        chosen = None
        if qty_idx is not None:
            for candidate in after:
                for other in after:
                    if other["idx"] == candidate["idx"]:
                        continue
                    if _multiplies_out(rows, qty_idx, candidate["idx"], other["idx"], thousands):
                        chosen = candidate["idx"]
                        mapping["total"] = other["idx"]  # kept: it is the file's own check on us
                        break
                if chosen is not None:
                    break
        mapping["unit_price"] = chosen if chosen is not None else after[0]["idx"]

    # 4. The description is the wordiest non-numeric column that is not already claimed.
    claimed = set(mapping.values())
    prose = [
        s for s in stats
        if s["idx"] not in claimed and s["numeric"] < 0.6 and s["unit"] < 0.5 and s["avg_len"] >= 8
    ]
    if prose:
        mapping["short_text"] = max(prose, key=lambda s: s["avg_len"])["idx"]

    # 5. The position number: a short, mostly-filled column left of the description — typically the
    #    first column, holding things like "01.02.0030".
    claimed = set(mapping.values())
    left_of_text = [
        s for s in stats
        if s["idx"] not in claimed
        and s["idx"] < mapping.get("short_text", width)
        and s["filled"] > 0
        and s["avg_len"] <= 16
    ]
    if left_of_text:
        mapping["oz"] = left_of_text[0]["idx"]

    return mapping


def _infer_columns(rows: list[list[str]]) -> dict[str, int]:
    """Infer the columns, tolerating a header row we could not name.

    Two passes, because an unrecognised header row is itself a row: with a short LV its text cells
    drag every column's numeric fraction below the threshold and nothing is identified. (Measured
    live: a file with one header row and one position failed, while the same file with two positions
    worked — the tests had two rows and hid it.) So try the rows as given, then again without the
    first one, and take the first pass that identifies a description and a quantity.
    """
    for candidate in (rows, rows[1:]):
        if not candidate:
            continue
        inferred = _infer_columns_by_content(candidate)
        if {"short_text", "quantity"} <= set(inferred):
            return inferred
    return _infer_columns_by_content(rows)


# DIN 276 (2018-12) cost groups. The first level is a hundred — 100 Grundstück … 800 Finanzierung —
# the second a ten (520), the third a unit (522), and a fourth may be appended (522.1 / 522-1).
_KG_NUMBER = re.compile(r"^([1-8])(\d)(\d)(?:[.\-/](\d+))?$")


def kg_level(number: str) -> int | None:
    """Which level of the DIN 276 hierarchy this number sits at, or None if it is not a cost group.

    100 → 1, 520 → 2, 522 → 3, 522.1 → 4. The shape of the number IS the level, which is what lets a
    document's own headings be read as a hierarchy without being told the depth in advance.
    """
    match = _KG_NUMBER.match((number or "").strip())
    if not match:
        return None
    _, tens, units, fourth = match.groups()
    if fourth:
        return 4
    if units != "0":
        return 3
    if tens != "0":
        return 2
    return 1


def _update_kg_stack(stack: list[str], number: str, label: str) -> list[str]:
    """Place a heading at its level and drop anything deeper — entering "530 Oberbau" ends the
    subgroups of 520. Returns a new list so each position can keep the path it was printed under."""
    level = kg_level(number)
    if level is None:
        return stack
    heading = f"{number} {label}".strip()
    updated = stack[: level - 1]
    while len(updated) < level - 1:
        updated.append("")  # a level the document skipped
    updated.append(heading)
    return updated


def _agreement(
    rows: list[list[str]], qty: int, unit_price: int, total: int, thousands: str | None
) -> float:
    """Fraction of rows where quantity × unit_price equals the total column."""
    checked = agreed = 0
    for row in rows:
        values = [_numeric_value(row[i], thousands) if i < len(row) else None
                  for i in (qty, unit_price, total)]
        q, u, t = values
        if q is None or u is None or t is None or q == 0:
            continue
        checked += 1
        if abs(q * u - t) <= Decimal("0.02"):
            agreed += 1
    return agreed / checked if checked else 0.0


def _verify_price_column(
    rows: list[list[str]], cols: dict[str, int], thousands: str | None
) -> dict[str, int]:
    """Check the chosen unit-price column against the file's own total column, and correct it.

    The header is a claim, not evidence. A column labelled "EP" can hold the line total, a PDF's
    columns can be extracted in an order the labels do not describe, and taking a total for a rate
    multiplies the whole bid by the quantity — the fault a bidder reported, where our "unit price"
    turned out to be exactly quantity × the real one. The file settles it: quantity × unit price must
    equal the total it prints. If the current choice fails that test and another numeric column
    passes, switch to the column the arithmetic supports.
    """
    qty, total = cols.get("quantity"), cols.get("total")
    if qty is None or total is None or not rows:
        return cols

    current = cols.get("unit_price")
    if current is not None and _agreement(rows, qty, current, total, thousands) >= 0.8:
        return cols  # the file confirms it

    width = max(len(r) for r in rows)
    best, best_score = current, 0.0
    for idx in range(width):
        if idx in (qty, total):
            continue
        score = _agreement(rows, qty, idx, total, thousands)
        if score > best_score:
            best, best_score = idx, score
    if best is not None and best_score >= 0.8:
        cols["unit_price"] = best
    return cols


def _sample(rows: list[list[str]], limit: int = 3) -> str:
    """A short, readable echo of what was actually read — so a failure says what it saw rather than
    only that it failed."""
    shown = [" | ".join(c for c in row if c.strip())[:120] for row in rows[:limit] if any(row)]
    return "; ".join(f"[{s}]" for s in shown) or "(no readable rows)"


def positions_from_rows(
    header: list[str], rows: list[list[str]], *, columns: dict[str, int] | None = None
) -> tuple[list[Position], list[Entry]]:
    """Turn a header + data rows into positions. The header decides the columns; anything it does not
    label is inferred from the data (`_infer_columns_by_content`). Pass `columns` to supply the
    mapping outright, for a file with no header row at all. The position number is generated if the
    file has none, and the price may be blank — the human fills it before export."""
    cols = dict(columns) if columns is not None else _map_columns(header)
    missing = {"short_text", "quantity", "unit"} - set(cols)
    if missing and columns is None:
        # Fill only what the header did not name, so an explicit label always wins over a guess.
        inferred = _infer_columns(rows)
        for field in missing:
            if field in inferred and inferred[field] not in cols.values():
                cols[field] = inferred[field]

    # A unit column is nice to have, not essential — a .x84 position can carry an empty QU, and some
    # LVs put the unit inside the description. A description and a quantity are the real minimum.
    for required in ("short_text", "quantity"):
        if required not in cols:
            raise NoPositionsError(
                f"could not identify a '{required}' column. Headers read: {header}. "
                f"First rows: {_sample(rows)}"
            )

    # One convention for the whole file: "1.180" is 1180 in a German LV and 1.18 in an English one,
    # and the file itself says which it is (see detect_thousands_separator).
    thousands = detect_thousands_separator(_all_cells(rows))

    # Never trust the price column on a label alone — make the file's own totals confirm it.
    cols = _verify_price_column(rows, cols, thousands)

    def cell(row: list[str], field: str) -> str | None:
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    positions: list[Position] = []
    entries: list[Entry] = []
    continuations: list[list[str]] = []  # extra description lines belonging to positions[-1]
    kg_stack: list[str] = []  # the DIN 276 headings this part of the document sits under
    for n, row in enumerate(rows, start=1):
        desc = (cell(row, "short_text") or "").strip()
        qty_raw = cell(row, "quantity")
        amount_raw = (cell(row, "teilbetrag") or cell(row, "unit_price") or "").strip()
        total_raw = (cell(row, "total") or "").strip()
        if not desc and not (qty_raw and qty_raw.strip()) and not total_raw:
            continue  # a blank or separator row
        quantity, split_unit = parse_quantity(qty_raw, thousands)
        oz = (cell(row, "oz") or "").strip()

        if quantity is None:
            # No quantity: this row is not a position. Three cases, and telling them apart is what
            # rebuilds the document:
            #   * a DIN 276 cost-group heading ("520  Gründung, Unterbau") — remembered, so the
            #     positions below inherit it, AND carried across with its own figures;
            #   * a CONTINUATION of the position above (no number of its own), which is where a
            #     German LV wraps its Langtext and prints the DIN references;
            #   * any other row that carries something — a source entry such as "1  LV Freiflächen"
            #     — which is carried across as it stands.
            level = kg_level(oz)
            if level is not None:
                kg_stack = _update_kg_stack(kg_stack, oz, desc)
                entries.append(
                    Entry(kind="kg", number=oz, text=desc, teilbetrag_ep=amount_raw,
                          gesamt=total_raw, level=level, kg=tuple(kg_stack))
                )
            elif desc and not oz and positions:
                continuations[-1].append(desc)
            elif desc or total_raw:
                entries.append(
                    Entry(kind="entry", number=oz, text=desc, teilbetrag_ep=amount_raw,
                          gesamt=total_raw, kg=tuple(kg_stack))
                )
            continue

        entries.append(
            Entry(kind="position", number=oz, text=desc,
                  menge_einheit=(qty_raw or "").strip(),
                  teilbetrag_ep=amount_raw, gesamt=total_raw, kg=tuple(kg_stack))
        )

        unit = (cell(row, "unit") or "").strip() or split_unit
        long_text = (cell(row, "long_text") or "").strip()
        section = (cell(row, "section") or "").strip()
        positions.append(
            Position(
                oz=oz or f"{n * 10:04d}",
                short_text=desc,
                quantity=quantity,
                unit=unit,
                unit_price=_maybe_decimal(cell(row, "unit_price"), thousands=thousands),
                source_total=_maybe_decimal(cell(row, "total"), thousands=thousands),
                teilbetrag=_maybe_decimal(cell(row, "teilbetrag"), thousands=thousands),
                long_text=long_text or None,
                section=section or (kg_stack[-1] if kg_stack else ""),
                kg=tuple(kg_stack),
                # Kept exactly as printed — the requirement is that these are taken over unchanged,
                # so what the table shows is the source's own text, not our re-rendering of it.
                quantity_text=(qty_raw or "").strip(),
                unit_price_text=(cell(row, "unit_price") or "").strip(),
                total_text=(cell(row, "total") or "").strip(),
                teilbetrag_text=(cell(row, "teilbetrag") or "").strip(),
            )
        )
        continuations.append([])

    if not positions:
        raise NoPositionsError("no priceable positions were found in the rows")

    # Fold the gathered continuation lines into each position's long text, keeping the order they
    # were printed in. The short text stays as the file wrote it, so the table still reads as the LV.
    positions = [
        replace(p, long_text="\n".join(filter(None, [p.long_text, *extra])) or None)
        for p, extra in zip(positions, continuations, strict=True)
    ]
    long_by_number = {p.oz: p.long_text for p in positions if p.long_text}
    entries = [
        replace(e, long_text=long_by_number.get(e.number)) if e.kind == "position" else e
        for e in entries
    ]
    return positions, entries


def read_csv(data: bytes, *, project_name: str) -> BillOfQuantities:
    """A delimited text export. The delimiter is sniffed (`;` is the German-Excel default, not `,`),
    and the first non-empty row is taken as the header."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")  # German exports are frequently Windows-1252/Latin-1
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.get_dialect("excel")
    reader = csv.reader(io.StringIO(text), dialect)
    grid = [row for row in reader if any(c.strip() for c in row)]
    if not grid:
        raise UnreadableFileError("the CSV is empty")
    # Same tail as every other grid source: find the header row rather than assuming row 1 (an export
    # may carry title lines above it, or none at all), and fall back to reading the columns from the
    # data when the labels are ones we do not know.
    positions, entries = _positions_from_grid(grid)
    return BillOfQuantities(
        project_name=project_name, positions=tuple(positions), entries=tuple(entries)
    )


def _cell_str(value: object) -> str:
    """A spreadsheet cell as text. `data_only` gives us computed values, so a number arrives as a
    float/int — render it without scientific notation or a trailing `.0`, since it becomes the string
    `parse_decimal` reads back."""
    if value is None:
        return ""
    if isinstance(value, float):
        return repr(value) if value != int(value) else str(int(value))
    return str(value).strip()


def _positions_from_grid(grid: list[list[str]]) -> tuple[list[Position], list[Entry]]:
    """Shared tail for every grid-shaped source (spreadsheet, Word table, PDF table). A real LV often
    has title rows above the table, so rather than assuming row 1, scan for the first row that maps to
    a description+quantity+unit and take it as the header. A header that repeats lower down (Word/PDF
    print it per page) is harmless: those rows carry no numeric quantity and are dropped."""
    # The page's own furniture — repeated headings, the footer, the VAT lines — is not part of the
    # bill of quantities and is dropped before anything is read from the table.
    rows = clean_grid(grid)
    if not rows:
        raise UnreadableFileError("there were no rows to read")

    # Best case: a row that names the columns outright. Take the first that identifies a description
    # and a quantity (a unit column is optional — see positions_from_rows).
    for i, candidate in enumerate(rows):
        if {"short_text", "quantity"} <= set(_map_columns(candidate)):
            try:
                return positions_from_rows(candidate, rows[i + 1 :])
            except NoPositionsError:
                continue  # that row looked like a header but yielded nothing — keep scanning

    # No recognisable header: many Word and PDF LVs simply do not print one. Read the columns from
    # the data instead, treating every row as a position and letting the non-numeric ones drop out.
    inferred = _infer_columns(rows)
    if {"short_text", "quantity"} <= set(inferred):
        try:
            return positions_from_rows([], rows, columns=inferred)
        except NoPositionsError:
            pass

    raise NoPositionsError(
        "could not identify the description and quantity columns in this file. "
        f"First rows read: {_sample(rows)}"
    )


def read_xlsx(data: bytes, *, project_name: str) -> BillOfQuantities:
    """Read the first worksheet of an .xlsx. `data_only=True` returns the last-computed values, so a
    priced LV with formula totals still yields numbers rather than "=A1*B1"."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a variety of types on a bad/other-format file
        raise UnreadableFileError(f"not a readable .xlsx: {exc}") from exc
    grid = [[_cell_str(c) for c in row] for row in wb.active.iter_rows(values_only=True)]
    positions, entries = _positions_from_grid(grid)
    return BillOfQuantities(
        project_name=project_name, positions=tuple(positions), entries=tuple(entries)
    )


def read_docx(data: bytes, *, project_name: str) -> BillOfQuantities:
    """Read a Word LV. The positions live in a real table, so this is structured extraction, not a
    guess: every table's rows are gathered into one grid and the header is found in it. A document
    that splits the LV across page-tables (each repeating the header) still reads correctly."""
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise UnreadableFileError(f"not a readable .docx: {exc}") from exc
    grid: list[list[str]] = []
    for table in document.tables:
        for row in table.rows:
            grid.append([cell.text.strip() for cell in row.cells])
    if not grid:
        raise NoPositionsError("the Word document has no tables to read positions from")
    positions, entries = _positions_from_grid(grid)
    return BillOfQuantities(
        project_name=project_name, positions=tuple(positions), entries=tuple(entries)
    )


def read_pdf(data: bytes, *, project_name: str) -> BillOfQuantities:
    """Read a PDF LV by extracting its tables with pdfplumber. This is the fragile path — a page's
    ruled table extracts cleanly, a borderless or scanned one may not — which is exactly why the
    result goes to the human-verify table, not straight to a .x84. If no table can be extracted, say
    so and point at the reliable formats rather than returning a garbled parse."""
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        raise UnreadableFileError(f"not a readable PDF: {exc}") from exc
    grid: list[list[str]] = []
    try:
        with pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    for row in table:
                        grid.append([(cell or "").strip() for cell in row])
    except Exception as exc:
        raise UnreadableFileError(f"could not read the PDF's tables: {exc}") from exc
    if not grid:
        raise NoPositionsError(
            "no table could be extracted from this PDF (it may be scanned or have no ruled table) — "
            "export the Leistungsverzeichnis to Excel or Word, or upload a GAEB file"
        )
    positions, entries = _positions_from_grid(grid)
    return BillOfQuantities(
        project_name=project_name, positions=tuple(positions), entries=tuple(entries)
    )


def _local(tag: str) -> str:
    """Strip the XML namespace from a tag, so an incoming GAEB file of any version (the namespace
    differs across 3.1/3.2/…) reads the same."""
    return tag.rsplit("}", 1)[-1]


def _find_text(item: ET.Element, *local_names: str) -> str | None:
    """First descendant whose local tag matches, namespace ignored."""
    for el in item.iter():
        if _local(el.tag) in local_names and el.text and el.text.strip():
            return el.text.strip()
    return None


def read_gaeb(data: bytes) -> BillOfQuantities:
    """Parse an existing GAEB DA XML file (.x81/.x83/.x84/…) into the model — the trivial, lossless
    source. Category labels become sections; each Item becomes a position."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnreadableFileError(f"not valid XML: {exc}") from exc
    if _local(root.tag) != "GAEB":
        raise UnreadableFileError("root element is not <GAEB>")

    project = _find_text(root, "NamePrj") or "GAEB import"

    positions: list[Position] = []

    def walk(node: ET.Element, section: str) -> None:
        for child in node:
            tag = _local(child.tag)
            if tag == "Item":
                # An Item is a leaf here: read its fields, but do not walk into its Description, or a
                # <span> inside the text would be mistaken for another position.
                short = _find_text(child, "TextOutlTxt", "span") or ""
                qty = parse_decimal(_find_text(child, "Qty")) or Decimal(0)
                unit = _find_text(child, "QU") or ""
                up = parse_decimal(_find_text(child, "UP"))
                oz = child.get("RNoPart") or child.get("RNo") or f"{len(positions) * 10 + 10:04d}"
                positions.append(
                    Position(oz=oz, short_text=short, quantity=qty, unit=unit,
                             unit_price=up, section=section)
                )
            elif tag == "BoQCtgy":
                label = ""
                for lbl in child:
                    if _local(lbl.tag) == "LblTx":
                        label = _find_text(lbl, "span", "LblTx") or ""
                        break
                walk(child, label or section)
            else:
                # Every other element (GAEB, Award, BoQ, BoQBody, Itemlist, …) is a container we
                # descend through, carrying the current section down.
                walk(child, section)

    walk(root, "")
    if not positions:
        raise NoPositionsError("the GAEB file contained no items")
    return BillOfQuantities(project_name=project, positions=tuple(positions))


def read_any(filename: str, data: bytes, *, project_name: str) -> BillOfQuantities:
    """Dispatch by extension, then by content magic bytes, to the right reader. The point is to route
    a bidder's file without asking them which format it is — an existing GAEB, a spreadsheet, or a CSV
    export. PDF is recognised but declined for now (Stage 2), as a clear message rather than a garbled
    parse."""
    name = (filename or "").lower().strip()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    head = data[:400].lstrip()

    if ext in _GAEB_EXTS or head.startswith(b"<?xml") or b"<GAEB" in head:
        try:
            return read_gaeb(data)
        except UnreadableFileError:
            if ext == "xml" or ext in _GAEB_EXTS:
                raise  # it claimed to be GAEB/XML and was not — say so, do not fall through

    # Extension first: .docx and .xlsx are both ZIP files (magic bytes "PK"), so only the extension
    # tells them apart reliably.
    if ext == "docx":
        return read_docx(data, project_name=project_name)
    if ext in {"xlsx", "xlsm"}:
        return read_xlsx(data, project_name=project_name)
    if ext in {"csv", "txt"}:
        return read_csv(data, project_name=project_name)
    if ext == "pdf" or data[:5] == b"%PDF-":
        return read_pdf(data, project_name=project_name)
    if ext == "xls":
        raise UnsupportedFormatError(
            "the old .xls format is not supported — save it as .xlsx and upload that"
        )

    # No/unknown extension: fall back on magic bytes. A ZIP could be either Office format, so try the
    # spreadsheet then the document; otherwise treat it as delimited text.
    if data[:2] == b"PK":
        try:
            return read_xlsx(data, project_name=project_name)
        except (UnreadableFileError, NoPositionsError):
            return read_docx(data, project_name=project_name)
    return read_csv(data, project_name=project_name)

# --- the raw view, for correcting a wrong guess ---------------------------------------------------
#
# Automatic column detection cannot be right for every document — a PDF's table is whatever the
# extractor made of the page, and a wrong guess quietly puts a position number in the quantity column.
# So the grid and the guess are both published: the interface shows the file as extracted and lets a
# person say which column is which, and that mapping wins over anything inferred here.


def read_grid(filename: str, data: bytes) -> list[list[str]]:
    """The file as a table of text, exactly as extraction produced it — no interpretation."""
    name = (filename or "").lower().strip()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    head = data[:400].lstrip()

    if ext == "docx":
        document = docx.Document(io.BytesIO(data))
        return [[c.text.strip() for c in row.cells] for t in document.tables for row in t.rows]
    if ext in {"xlsx", "xlsm"}:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return [[_cell_str(c) for c in row] for row in wb.active.iter_rows(values_only=True)]
    if ext == "pdf" or data[:5] == b"%PDF-":
        grid: list[list[str]] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    for row in table:
                        grid.append([(cell or "").strip() for cell in row])
        return grid
    if ext in _GAEB_EXTS or head.startswith(b"<?xml") or b"<GAEB" in head:
        boq = read_gaeb(data)
        return [
            [p.oz, p.short_text, f"{p.quantity} {p.unit}".strip(),
             "" if p.unit_price is None else str(p.unit_price),
             "" if p.source_total is None else str(p.source_total)]
            for p in boq.positions
        ]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.get_dialect("excel")
    return [row for row in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in row)]


# --- page furniture -------------------------------------------------------------------------------
#
# A PDF extractor sees the whole page, so a printed table arrives with its surroundings: the column
# headings repeated at the top of every page, and the footer carrying the document's own totals. Those
# are not rows of the bill of quantities, and a footer that lands in the table reads as a position.
#
# The VAT lines go with them. A Kostenberechnung states its total three times — net, the VAT on top,
# and the gross — and only the net belongs in an LV, so "zzgl. MwSt." and "Gesamt, Brutto" are
# dropped while "Gesamt, Netto" stays. `netto` is deliberately absent from the list below.
_FURNITURE_MARKERS = (
    "brutto", "mwst", "mehrwertsteuer", "umsatzsteuer", "ust.",
    "seite ", "page ", "kostenberechnung", "kostenschätzung",
)

# Two German amounts printed side by side and extracted as one cell: "229.622,15273.250" is the net
# total followed by the gross. The cents of the first end exactly where the digits of the second
# begin, which is what makes the split safe.
_GLUED_AMOUNTS = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})(?=\d)")


def _split_glued_amounts(cell: str) -> list[str]:
    """["229.622,15", "273.250"] from "229.622,15273.250"; the cell unchanged if it is not glued."""
    text = (cell or "").strip()
    parts = [part for part in _GLUED_AMOUNTS.split(text) if part]
    return parts if len(parts) > 1 else [text]


# A German amount, for finding the figures inside a cell that holds more than one.
_AMOUNT = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
# The German VAT rates. Used as evidence, not as arithmetic on the user's behalf: if one amount in a
# cell is another plus VAT, the second is the gross figure and can be recognised as such.
_VAT_RATES = (Decimal("1.19"), Decimal("1.07"))


def strip_vat(cell: str) -> str:
    """Remove the gross figure from a cell that carries both, and the VAT lines printed with it.

    The row-level filter is not enough on its own: an extractor often folds two printed lines into
    ONE cell, so "Außenanlagen und Freiflächen / Gesamt (inkl. MwSt. 19,0%), Brutto:" and its two
    amounts arrive together, on a row that carries a cost group number and must therefore be kept.
    The gross is identified by the arithmetic already in the document — it is the net plus VAT — and
    by the words printed beside it, never by position or by us computing anything new.
    """
    text = (cell or "").strip()
    if not text:
        return text

    lines = [
        line
        for line in text.splitlines()
        if not any(marker in line.lower() for marker in _FURNITURE_MARKERS)
    ]
    text = "\n".join(line for line in lines if line.strip()).strip()

    # Two amounts, whether printed apart or run together by the extractor ("229.622,15273.250").
    candidates = _split_glued_amounts(text.replace("\n", " ").strip())
    if len(candidates) != 2:
        candidates = _AMOUNT.findall(text.replace("\n", " "))
    if len(candidates) == 2:
        net = _maybe_decimal(candidates[0], thousands=".")
        gross = _maybe_decimal(candidates[1], thousands=".")
        if net and gross and any(
            abs(net * rate - gross) <= net * rate * Decimal("0.005") for rate in _VAT_RATES
        ):
            return candidates[0].strip()
    return text


# A page footer arrives interleaved: the extractor reads several printed lines at once and splits
# words across cells, so "Gesamt, Netto: / zzgl. MwSt.: / Gesamt, Brutto:" comes out as
# "Gesamtsum | me: KMZ, Köln | Gesamt, Nzzg | etto: 2wSt.:rutto:". Whole words no longer match, so the
# row is squashed to bare letters and digits first and recognised by these fragments.
_SQUASHED_MARKERS = ("rutto", "wst", "mehrwertsteuer", "umsatzsteuer")


def _squash(text: str) -> str:
    return re.sub(r"[^a-zäöüß0-9]", "", text.lower())


def _normalise_kg_number(cell: str) -> str:
    """"5 99" -> "599". A PDF extractor sometimes puts a space between the digits of a cost group
    number, and the split number is then not recognised as a cost group at all, so the whole
    hierarchy below it is lost. Only applied when closing the gap yields a real DIN 276 number, so a
    value that merely looks similar is never altered."""
    text = (cell or "").strip()
    if " " not in text or not re.fullmatch(r"\d[\d ]*\d", text):
        return text
    closed = re.sub(r"\s+", "", text)
    return closed if kg_level(closed) is not None else text


def _is_page_furniture(row: list[str]) -> bool:
    """True for a row that belongs to the page rather than to the bill of quantities.

    A row carrying a cost-group or position number is never furniture, whatever else it says — that
    guard is what stops a KG heading being dropped because the gross total was printed beside it.
    """
    cells = [(c or "").strip() for c in row]
    joined = " ".join(c.lower() for c in cells if c)
    if not joined:
        return True
    if any(kg_level(c) is not None or _POSITION_NUMBER.match(c) for c in cells):
        return False
    if any(marker in joined for marker in _FURNITURE_MARKERS):
        return True
    # The same check against the row squashed to bare characters, for a footer whose words the
    # extractor broke apart ("…Nzzg | etto: 2wSt.:rutto:" still contains "wst" and "rutto").
    squashed = _squash(joined)
    return any(marker in squashed for marker in _SQUASHED_MARKERS)


def _looks_like_a_heading_row(row: list[str]) -> bool:
    """A row that names our fields rather than carrying values — the table's column headings."""
    cells = [(c or "").strip() for c in row]
    return len(_map_columns(cells)) >= 3 and not any(_is_numeric(c) for c in cells if c)


# The document's closing total ("Gesamt, Netto: …"), as distinct from a cost group's total.
_TOTAL_LABELS = ("gesamt", "summe", "endsumme", "gesamtsumme")


def _tidy_document_total(row: list[str]) -> list[str]:
    """Clean a closing-total row: keep the label and its figure, drop the page furniture beside them.

    A footer is printed across the page, so the extractor puts whatever else is down there — the
    office name, the city — into the first column, where it reads as a position number. The row is
    worth keeping (it is the document's own net total) but only the part that belongs to it.
    """
    cells = [(c or "").strip() for c in row]
    if any(kg_level(c) is not None or _POSITION_NUMBER.match(c) for c in cells):
        return cells  # a cost group's total, not the document's — leave it alone
    text = " ".join(cells).lower()
    if not any(label in text for label in _TOTAL_LABELS):
        return cells
    # Blank any leading cell that is neither the label nor a figure.
    tidied = list(cells)
    for i, cell in enumerate(tidied):
        if not cell or _is_numeric(cell):
            continue
        if any(label in cell.lower() for label in _TOTAL_LABELS):
            break
        tidied[i] = ""
    return tidied


def clean_grid(grid: list[list[str]]) -> list[list[str]]:
    """The extracted table with the page's own furniture removed and glued amounts separated.

    Widths are respected: a cell is split only when doing so brings the row to the width the rest of
    the document uses, so a genuine value is never broken apart to make a row look tidy.
    """
    rows = [row for row in grid if any((c or "").strip() for c in row)]
    if not rows:
        return []
    widths: dict[int, int] = {}
    for row in rows:
        widths[len(row)] = widths.get(len(row), 0) + 1
    common = max(widths, key=lambda w: widths[w])

    cleaned: list[list[str]] = []
    seen_headings: set[str] = set()
    for original in rows:
        # Clean inside the cells first: a kept row may still be carrying the gross figure and the
        # VAT line folded in with it.
        row = [strip_vat(cell) for cell in original]
        if not any(cell.strip() for cell in row):
            continue
        # The cost-group number sits in the leading cell; close a gap the extractor put in it.
        for i, cell in enumerate(row):
            if cell.strip():
                row[i] = _normalise_kg_number(cell)
                break
        if _is_page_furniture(row):
            continue
        if _looks_like_a_heading_row(row):
            # The first one names the columns and must survive; a PDF reprints it on every page, and
            # those repeats are page furniture like any other. A heading is never tidied as a total —
            # "Gesamt EUR" is the name of a column, not a sum.
            signature = "|".join((c or "").strip().lower() for c in row)
            if signature in seen_headings:
                continue
            seen_headings.add(signature)
        else:
            row = _tidy_document_total(row)
            # A closing total is printed once per page too; keep the first and drop the repeats.
            if any(label in " ".join(row).lower() for label in _TOTAL_LABELS) and not any(
                kg_level(c) is not None or _POSITION_NUMBER.match(c) for c in row
            ):
                signature = "total:" + "|".join(c.strip().lower() for c in row)
                if signature in seen_headings:
                    continue
                seen_headings.add(signature)
        if len(row) < common:
            widened: list[str] = []
            for cell in row:
                widened.extend(_split_glued_amounts(cell))
            if len(widened) == common:
                row = widened
        cleaned.append(row)
    return cleaned


def detect_columns(grid: list[list[str]]) -> tuple[dict[str, int], int]:
    """Our best guess at which column is which, and the row the data starts on.

    Returned rather than only used, so the interface can show the guess and let it be overruled.
    """
    rows = clean_grid(grid)
    if not rows:
        return {}, 0
    for i, candidate in enumerate(rows):
        mapped = _map_columns(candidate)
        if {"short_text", "quantity"} <= set(mapped):
            thousands = detect_thousands_separator(_all_cells(rows[i + 1 :]))
            return _verify_price_column(rows[i + 1 :], mapped, thousands), i + 1
    inferred = _infer_columns(rows)
    thousands = detect_thousands_separator(_all_cells(rows))
    return _verify_price_column(rows, inferred, thousands), 0
