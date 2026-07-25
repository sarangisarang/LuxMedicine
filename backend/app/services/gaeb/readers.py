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

from app.services.gaeb.model import BillOfQuantities, Position


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
    ),
    "short_text": (
        "kurztext", "bezeichnung", "beschreibung", "leistungsbeschreibung", "leistung",
        "leistungstext", "positionstext", "artikel", "benennung", "text", "description",
        "title", "gegenstand",
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


def _is_numeric(value: str, thousands: str | None = None) -> bool:
    return _maybe_decimal(value, thousands=thousands) is not None


def _all_cells(rows: list[list[str]]) -> list[str]:
    return [c for row in rows for c in row]


# "720 m2", "25 Stk", "1.180 m" — a quantity and its unit printed in one column, which is how a PDF
# or Word LV usually sets them. Read as a single value it is not a number at all and the whole row
# would be dropped, so the two are separated here.
# The separator must be whitespace, and the trailing token is checked against the unit vocabulary
# below — so "720 m2" splits (a unit may contain digits, as m2 and m3 do) while "1.180" or "12x4"
# never does.
_QTY_WITH_UNIT = re.compile(r"^([\d.,]+)\s+(\S{1,6})$")


def _split_quantity_unit(text: str) -> tuple[str, str]:
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
            values.append(_maybe_decimal(row[idx], thousands=thousands) if idx < len(row) else None)
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
                        mapping["_total"] = other["idx"]  # remembered only to keep it out of the way
                        break
                if chosen is not None:
                    break
        mapping["unit_price"] = chosen if chosen is not None else after[0]["idx"]
        mapping.pop("_total", None)

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


def _sample(rows: list[list[str]], limit: int = 3) -> str:
    """A short, readable echo of what was actually read — so a failure says what it saw rather than
    only that it failed."""
    shown = [" | ".join(c for c in row if c.strip())[:120] for row in rows[:limit] if any(row)]
    return "; ".join(f"[{s}]" for s in shown) or "(no readable rows)"


def positions_from_rows(
    header: list[str], rows: list[list[str]], *, columns: dict[str, int] | None = None
) -> list[Position]:
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

    def cell(row: list[str], field: str) -> str | None:
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    positions: list[Position] = []
    continuations: list[list[str]] = []  # extra description lines belonging to positions[-1]
    for n, row in enumerate(rows, start=1):
        desc = (cell(row, "short_text") or "").strip()
        qty_raw = cell(row, "quantity")
        if not desc and not (qty_raw and qty_raw.strip()):
            continue  # a blank or separator row
        quantity = _maybe_decimal(qty_raw, thousands=thousands)
        # "720 m2" in one cell: separate the unit rather than lose the whole row.
        split_unit = ""
        if quantity is None and qty_raw:
            number, split_unit = _split_quantity_unit(qty_raw)
            quantity = _maybe_decimal(number, thousands=thousands) if split_unit else None
        oz = (cell(row, "oz") or "").strip()

        if quantity is None:
            # No quantity: this row is not a position of its own. It is either a heading (which
            # carries its own OZ) or — the case that was silently losing text — a CONTINUATION of the
            # position above it. A German LV wraps the Langtext over several lines, and that is where
            # the DIN references and the technical qualifiers live. Dropping them threw away exactly
            # the part a bidder must read.
            if desc and not oz and positions:
                continuations[-1].append(desc)
            continue

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
                long_text=long_text or None,
                section=section,
            )
        )
        continuations.append([])

    if not positions:
        raise NoPositionsError("no priceable positions were found in the rows")

    # Fold the gathered continuation lines into each position's long text, keeping the order they
    # were printed in. The short text stays as the file wrote it, so the table still reads as the LV.
    return [
        replace(p, long_text="\n".join(filter(None, [p.long_text, *extra])) or None)
        for p, extra in zip(positions, continuations, strict=True)
    ]


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
    return BillOfQuantities(
        project_name=project_name, positions=tuple(_positions_from_grid(grid))
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


def _positions_from_grid(grid: list[list[str]]) -> list[Position]:
    """Shared tail for every grid-shaped source (spreadsheet, Word table, PDF table). A real LV often
    has title rows above the table, so rather than assuming row 1, scan for the first row that maps to
    a description+quantity+unit and take it as the header. A header that repeats lower down (Word/PDF
    print it per page) is harmless: those rows carry no numeric quantity and are dropped."""
    rows = [row for row in grid if any((c or "").strip() for c in row)]
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
    return BillOfQuantities(project_name=project_name, positions=tuple(_positions_from_grid(grid)))


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
    return BillOfQuantities(project_name=project_name, positions=tuple(_positions_from_grid(grid)))


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
    return BillOfQuantities(project_name=project_name, positions=tuple(_positions_from_grid(grid)))


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
