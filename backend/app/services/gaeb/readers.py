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
import xml.etree.ElementTree as ET
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


def parse_decimal(raw: str | None) -> Decimal | None:
    """Read a quantity or price written in either German or English convention. Returns None for a
    blank cell (an unpriced position is valid in the model; the writer decides if that blocks .x84)."""
    if raw is None:
        return None
    s = str(raw).strip()
    for junk in (" ", " ", "€", "EUR", "\t"):
        s = s.replace(junk, "")
    if not s:
        return None
    if "," in s and "." in s:
        # Whichever separator comes last is the decimal point; the other is thousands.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # 1.234,56 -> 1234.56
        else:
            s = s.replace(",", "")  # 1,234.56 -> 1234.56
    elif "," in s:
        s = s.replace(",", ".")  # 1234,56 -> 1234.56
    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise UnreadableFileError(f"not a number: {raw!r}") from exc


# Canonical field -> the header labels that mean it (lower-cased, German first). A column matches if
# its header equals or starts with one of these — enough for the common exports without a mapping UI.
_HEADERS: dict[str, tuple[str, ...]] = {
    "oz": ("oz", "ordnungszahl", "positionsnummer", "position", "pos", "nummer", "nr", "item"),
    "short_text": ("kurztext", "bezeichnung", "beschreibung", "leistung", "text", "description", "title"),
    "quantity": ("menge", "mengenansatz", "anzahl", "qty", "quantity"),
    "unit": ("einheit", "mengeneinheit", "me", "unit", "qu"),
    "unit_price": ("einheitspreis", "einzelpreis", "ep", "up", "preis", "unitprice", "unit price"),
    "long_text": ("langtext", "detailtext", "spezifikation", "long text"),
    "section": ("titel", "los", "gruppe", "gewerk", "abschnitt", "section"),
}


def _map_columns(header: list[str]) -> dict[str, int]:
    """Match each header cell to a canonical field. First column wins a field, so a stray later
    'Position' does not steal it from the real one."""
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        label = (cell or "").strip().lower()
        if not label:
            continue
        for field, names in _HEADERS.items():
            if field in mapping:
                continue
            if any(label == n or label.startswith(n) for n in names):
                mapping[field] = idx
                break
    return mapping


def positions_from_rows(header: list[str], rows: list[list[str]]) -> list[Position]:
    """Turn a header + data rows into positions. Requires at least a description, quantity and unit
    to be identifiable; the position number is generated if the file has none, and the price may be
    blank (the human fills it before export)."""
    cols = _map_columns(header)
    for required in ("short_text", "quantity", "unit"):
        if required not in cols:
            raise NoPositionsError(
                f"could not find a '{required}' column among headers: {header}"
            )

    def cell(row: list[str], field: str) -> str | None:
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    positions: list[Position] = []
    for n, row in enumerate(rows, start=1):
        desc = (cell(row, "short_text") or "").strip()
        qty_raw = cell(row, "quantity")
        if not desc and not (qty_raw and qty_raw.strip()):
            continue  # a blank or separator row
        quantity = parse_decimal(qty_raw)
        if quantity is None:
            continue  # a heading row with text but no quantity — not a priceable position
        oz = (cell(row, "oz") or "").strip() or f"{n * 10:04d}"
        unit = (cell(row, "unit") or "").strip()
        long_text = (cell(row, "long_text") or "").strip() or None
        section = (cell(row, "section") or "").strip()
        positions.append(
            Position(
                oz=oz,
                short_text=desc,
                quantity=quantity,
                unit=unit,
                unit_price=parse_decimal(cell(row, "unit_price")),
                long_text=long_text,
                section=section,
            )
        )
    if not positions:
        raise NoPositionsError("no priceable positions were found in the rows")
    return positions


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
    all_rows = [row for row in reader if any(c.strip() for c in row)]
    if not all_rows:
        raise UnreadableFileError("the CSV is empty")
    header, rows = all_rows[0], all_rows[1:]
    positions = positions_from_rows(header, rows)
    return BillOfQuantities(project_name=project_name, positions=tuple(positions))


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
    for i, candidate in enumerate(rows):
        if {"short_text", "quantity", "unit"} <= set(_map_columns(candidate)):
            return positions_from_rows(candidate, rows[i + 1 :])
    raise NoPositionsError(
        "no header row with a description, quantity and unit column was found"
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
