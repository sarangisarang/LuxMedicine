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
    parse_decimal,
    read_any,
)
from app.services.gaeb.writer import UnpricedOfferError, write_x84

router = APIRouter(prefix="/gaeb", tags=["gaeb"])

MAX_UPLOAD = 25 * 1024 * 1024  # a spreadsheet or GAEB file is small; a 25 MB cap is generous


class PositionDTO(BaseModel):
    """Numbers travel as strings, not floats: the client edits them in text fields and a bid total is
    money, so the server parses them with `Decimal` (German or English notation) rather than trusting
    a float across the wire."""

    oz: str = ""
    short_text: str = ""
    quantity: str = ""
    unit: str = ""
    unit_price: str | None = None
    long_text: str | None = None
    section: str = ""
    # What the source file printed as this line's total, if it had such a column. Read-only evidence
    # for the verify table — the .x84 always derives its own total from Qty × UP.
    source_total: str | None = None


class BoQDTO(BaseModel):
    project_name: str = "Leistungsverzeichnis"
    currency: str = "EUR"
    positions: list[PositionDTO] = Field(default_factory=list)


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
                oz=p.oz,
                short_text=p.short_text,
                quantity=_fmt_quantity(p.quantity),
                unit=p.unit,
                unit_price=None if p.unit_price is None else _fmt(p.unit_price, 2),
                long_text=p.long_text,
                section=p.section,
                source_total=None if p.source_total is None else _fmt(p.source_total, 2),
            )
            for p in boq.positions
        ],
    )


def _from_dto(dto: BoQDTO) -> BillOfQuantities:
    positions: list[Position] = []
    for i, p in enumerate(dto.positions, start=1):
        if not p.short_text.strip() and not p.quantity.strip():
            continue  # a blank row the user left in the table
        try:
            quantity = parse_decimal(p.quantity)
            unit_price = parse_decimal(p.unit_price)
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
                unit=p.unit.strip(),
                unit_price=unit_price,
                long_text=(p.long_text or "").strip() or None,
                section=p.section.strip(),
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
    return _to_dto(boq)


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
