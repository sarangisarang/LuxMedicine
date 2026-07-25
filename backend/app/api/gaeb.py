"""GAEB DA XML conversion endpoints (Baurecht side): turn any bill-of-quantities file into a `.x84`.

Two steps, deliberately not one. `POST /gaeb/parse` reads an uploaded file into a list of positions
and hands it back for a human to check and price; `POST /gaeb/export` takes the confirmed positions
and returns the `.x84` to download. Nothing is exported that the caller has not seen and approved — a
wrong unit price in a construction bid is real money, the same rule as the clinical side.

Authenticated like everything else. Stateless: no database, no tenant writes — a conversion tool that
happens to live behind the same login.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.core.auth import Clinician, current_clinician
from app.services.gaeb.model import BillOfQuantities, Position
from app.services.gaeb.readers import (
    NoPositionsError,
    UnreadableFileError,
    UnsupportedFormatError,
    clean_grid,
    detect_columns,
    detect_thousands_separator,
    parse_decimal,
    parse_quantity,
    read_any,
    read_grid,
    split_quantity_unit,
)
from app.services.gaeb.writer import UnpricedOfferError, write_x84

router = APIRouter(prefix="/gaeb", tags=["gaeb"])

MAX_UPLOAD = 25 * 1024 * 1024  # a spreadsheet or GAEB file is small; a 25 MB cap is generous


class PositionDTO(BaseModel):
    """One row of the table, in the order the specification asks for:
    KG → KG level 2 → 3 → 4 → Positionsnummer → Leistungstext → Menge → Einheit → Teilbetrag → EP →
    Gesamt EUR.

    Every value travels as the STRING the source file printed. Nothing here is computed: quantities,
    units, partial amounts, unit prices and totals are taken over unchanged, which is both the
    requirement and the only way the table can be checked against the document it came from."""

    # The DIN 276 cost-group path this position was printed under, outermost first (up to 4 levels).
    kg: list[str] = Field(default_factory=list)
    oz: str = ""
    short_text: str = ""
    quantity: str = ""
    unit: str = ""
    teilbetrag: str | None = None
    unit_price: str | None = None
    total: str | None = None
    long_text: str | None = None
    section: str = ""


class EntryDTO(BaseModel):
    """One row of the source document, mirroring its columns: KG/OZ, DIN 276 / Quelleinträge,
    Menge/Einheit, Teilbetrag / EP, Gesamt EUR. `kind` says whether the row is a cost-group heading,
    a source entry or a position — that is what lets the table be rendered the way the document
    reads. Every value is the source's own text; nothing here is computed."""

    kind: str = "position"
    number: str = ""
    text: str = ""
    menge_einheit: str = ""
    teilbetrag_ep: str = ""
    gesamt: str = ""
    level: int = 0
    kg: list[str] = Field(default_factory=list)
    long_text: str | None = None


class BoQDTO(BaseModel):
    project_name: str = "Leistungsverzeichnis"
    currency: str = "EUR"
    positions: list[PositionDTO] = Field(default_factory=list)
    # The document as it stands, for display. `positions` is the subset that can become .x84 items.
    entries: list[EntryDTO] = Field(default_factory=list)
    # The file exactly as extraction produced it, plus the column mapping we guessed. Published so a
    # wrong guess can be seen and corrected in the interface rather than argued with.
    grid: list[list[str]] = Field(default_factory=list)
    mapping: dict[str, int] = Field(default_factory=dict)
    data_starts_at: int = 0


def _fmt(value: Decimal | None, places: int) -> str:
    return "" if value is None else f"{value:.{places}f}"


def _fmt_quantity(value: Decimal | None) -> str:
    """A quantity without pointless trailing zeros: 720, 12.5 — not "720.000".

    Cosmetic but not trivial: to a German reader "720.000" is seven hundred and twenty THOUSAND,
    because a dot groups thousands there. Showing a padded quantity in a German construction tool
    invites exactly the misreading this converter has to avoid.
    """
    if value is None:
        return ""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _to_dto(boq: BillOfQuantities) -> BoQDTO:
    return BoQDTO(
        project_name=boq.project_name,
        currency=boq.currency,
        positions=[
            PositionDTO(
                kg=list(p.kg),
                oz=p.oz,
                short_text=p.short_text,
                # The source's own text wherever it exists; a formatted value only for an input that
                # carries none (a GAEB import, whose numbers are already typed).
                quantity=p.quantity_text or _fmt_quantity(p.quantity),
                unit=p.unit,
                teilbetrag=p.teilbetrag_text or (None if p.teilbetrag is None else _fmt(p.teilbetrag, 2)),
                unit_price=p.unit_price_text or (None if p.unit_price is None else _fmt(p.unit_price, 2)),
                total=p.total_text or (None if p.source_total is None else _fmt(p.source_total, 2)),
                long_text=p.long_text,
                section=p.section,
            )
            for p in boq.positions
        ],
        entries=[
            EntryDTO(
                kind=e.kind,
                number=e.number,
                text=e.text,
                menge_einheit=e.menge_einheit,
                teilbetrag_ep=e.teilbetrag_ep,
                gesamt=e.gesamt,
                level=e.level,
                kg=list(e.kg),
                long_text=e.long_text,
            )
            for e in boq.entries
        ],
    )


def _entry_to_position(e: EntryDTO, index: int, thousands: str | None) -> Position | None:
    """Turn one displayed row back into an exportable position, or None if it is not one.

    A heading is structure, not an item: only rows the reader called a position become .x84 items.
    The values are parsed from the text the table holds — which is the source's own text, possibly
    corrected by hand — and never recalculated."""
    if e.kind != "position":
        return None
    # The same reader the file went through, so a cell accepted on the way in ("25m3", "1Ps...")
    # cannot be rejected as "not a number" on the way out.
    quantity, split_unit = parse_quantity(e.menge_einheit, thousands)
    return Position(
        oz=e.number.strip() or f"{index * 10:04d}",
        short_text=e.text.strip(),
        quantity=quantity if quantity is not None else Decimal(0),
        unit=split_unit,
        unit_price=parse_decimal(e.teilbetrag_ep, thousands=thousands),
        source_total=parse_decimal(e.gesamt, thousands=thousands),
        long_text=(e.long_text or "").strip() or None,
        section=e.kg[-1] if e.kg else "",
        kg=tuple(e.kg),
    )


def _from_dto(dto: BoQDTO) -> BillOfQuantities:
    # The table holds the source file's own text, so it is read back with the same convention the
    # file was written in — "1.180" is 1180 in a German document — and a quantity may still carry
    # its unit ("720 m2"). The values themselves are never recomputed; they are only typed so the
    # .x84 can carry them.
    thousands = detect_thousands_separator(
        [t for e in dto.entries for t in (e.menge_einheit, e.teilbetrag_ep, e.gesamt)]
        or [t for p in dto.positions for t in (p.quantity, p.unit_price or "", p.total or "")]
    )

    # The table is the document, so the export reads it: every row the user confirmed, with the
    # headings acting as structure and the positions becoming items.
    if dto.entries:
        rows: list[Position] = []
        for i, e in enumerate(dto.entries, start=1):
            try:
                position = _entry_to_position(e, i, thousands)
            except UnreadableFileError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"row {i} ({e.number or e.text!r}): {exc}",
                ) from exc
            if position is not None:
                rows.append(position)
        return BillOfQuantities(
            project_name=dto.project_name.strip() or "Leistungsverzeichnis",
            currency=dto.currency.strip() or "EUR",
            positions=tuple(rows),
        )

    positions: list[Position] = []
    for i, p in enumerate(dto.positions, start=1):
        if not p.short_text.strip() and not p.quantity.strip():
            continue  # a blank row the user left in the table
        quantity_text, split_unit = split_quantity_unit(p.quantity)
        try:
            quantity = parse_decimal(quantity_text, thousands=thousands)
            unit_price = parse_decimal(p.unit_price, thousands=thousands)
            source_total = parse_decimal(p.total, thousands=thousands)
            teilbetrag = parse_decimal(p.teilbetrag, thousands=thousands)
        except UnreadableFileError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"position {i} ({p.oz or p.short_text!r}): {exc}",
            ) from exc
        positions.append(
            Position(
                oz=p.oz.strip() or f"{i * 10:04d}",
                short_text=p.short_text.strip(),
                quantity=quantity if quantity is not None else Decimal(0),
                unit=p.unit.strip() or split_unit,
                unit_price=unit_price,
                source_total=source_total,
                teilbetrag=teilbetrag,
                long_text=(p.long_text or "").strip() or None,
                section=p.section.strip(),
                kg=tuple(p.kg),
            )
        )
    return BillOfQuantities(
        project_name=dto.project_name.strip() or "Leistungsverzeichnis",
        currency=dto.currency.strip() or "EUR",
        positions=tuple(positions),
    )


@router.post("/parse", response_model=BoQDTO)
async def parse_upload(
    file: UploadFile = File(...),
    project_name: str = Form("Leistungsverzeichnis"),
    _: Clinician = Depends(current_clinician),
) -> BoQDTO:
    """Read an uploaded LV (Excel, CSV, or an existing GAEB file) into positions for the user to
    verify. Does not write anything or produce a .x84 — that is the explicit second step."""
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file is too large")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the file is empty")
    try:
        boq = read_any(file.filename or "", data, project_name=project_name.strip() or "Leistungsverzeichnis")
    except UnsupportedFormatError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except (UnreadableFileError, NoPositionsError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    dto = _to_dto(boq)
    # Publish the raw extraction too. If the guess below put a column in the wrong place, this is
    # what lets a person fix it — without it the only recourse is to describe the problem and wait.
    try:
        grid = clean_grid(
            [[(c or "").strip() for c in row] for row in read_grid(file.filename or "", data)]
        )
        mapping, start = detect_columns(grid)
    except Exception:  # a raw view is a convenience; never fail the conversion for it
        grid, mapping, start = [], {}, 0
    dto.grid = grid
    dto.mapping = mapping
    dto.data_starts_at = start
    return dto


def _download_name(project_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", project_name.strip()).strip("_") or "angebot"
    return f"{slug}.x84"


@router.post("/export")
async def export_x84(
    boq: BoQDTO,
    _: Clinician = Depends(current_clinician),
) -> Response:
    """Generate the `.x84` from confirmed positions and return it as a download. 422 if any position
    is still unpriced — a .x84 is an offer, and a silent zero price is not one."""
    model = _from_dto(boq)
    if not model.positions:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no positions to export")
    try:
        xml = write_x84(model, generated_at=datetime.now(timezone.utc))
    except UnpricedOfferError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    filename = _download_name(model.project_name)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
