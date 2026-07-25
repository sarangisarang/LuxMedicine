"""Emit a GAEB DA XML `.x84` (Angebotsabgabe) from a `BillOfQuantities`.

The structure follows GAEB DA XML 3.2: GAEB → Award(DP=84) → BoQ → BoQBody → (BoQCtgy)* → Itemlist →
Item(Qty, QU, Description, UP, IT), closed by the grand Total.

⚠️ ONE thing to confirm against a real `.x84` from the target program (ORCA / California / iTWO / …):
the **namespace + version** below. GAEB DA XML ships several version-specific namespaces, and a
consumer that expects 3.1 will reject a 3.2 document and vice-versa. If the target tool refuses the
file, `GAEB_NAMESPACE`/`GAEB_VERSION`/`GAEB_VERSDATE` are the values to change — the element structure
does not. Everything else here is schema-standard.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

from app.services.gaeb.model import BillOfQuantities, Position, round_qty

# --- the version knobs to confirm against a real target file (see module docstring) ---------------
GAEB_NAMESPACE = "http://www.gaeb.de/GAEB_DA_XML/DA84/3.2"
GAEB_VERSION = "3.2"
GAEB_VERSDATE = "2013-06"
# Data phase 84 = Angebotsabgabe (offer submission). This is what makes the file an ".x84".
DATA_PHASE_OFFER = "84"

# A fixed namespace so the ID we mint for a position is stable across runs (a re-export of the same
# LV yields the same file) rather than random — deterministic output is testable and diff-able.
_ID_NAMESPACE = uuid.UUID("0f9a1e6c-1c2a-4d5b-9e7f-6a5b4c3d2e10")


class UnpricedOfferError(ValueError):
    """A `.x84` is an offer; every position must carry a unit price. Refuse rather than emit a
    document that reads as a complete bid with silent zero prices."""


def _q(tag: str) -> str:
    return f"{{{GAEB_NAMESPACE}}}{tag}"


def _fmt_qty(value: Decimal) -> str:
    # Three decimals, '.' separator — DA XML is culture-invariant, never a locale comma.
    return f"{round_qty(value):.3f}"


def _fmt_money(value: Decimal) -> str:
    return f"{value:.2f}"


def _text_block(parent: ET.Element, tag: str, content: str) -> None:
    """GAEB wraps a text in <tag><p><span>…</span></p></tag>. Empty content still gets the element
    so the schema's required children are present."""
    node = ET.SubElement(parent, _q(tag))
    p = ET.SubElement(node, _q("p"))
    span = ET.SubElement(p, _q("span"))
    span.text = content or ""


def _item_element(parent: ET.Element, pos: Position) -> None:
    item = ET.SubElement(parent, _q("Item"))
    item.set("ID", str(uuid.uuid5(_ID_NAMESPACE, pos.oz or pos.short_text)))
    # RNoPart carries the position number. Full OZ-mask decomposition (splitting "01.02.0030" across
    # hierarchy levels) is a later refinement; the whole OZ here is accepted by common readers.
    item.set("RNoPart", pos.oz)

    ET.SubElement(item, _q("Qty")).text = _fmt_qty(pos.quantity)
    ET.SubElement(item, _q("QU")).text = pos.unit

    description = ET.SubElement(item, _q("Description"))
    complete = ET.SubElement(description, _q("CompleteText"))
    outline = ET.SubElement(complete, _q("OutlineText"))
    outl = ET.SubElement(outline, _q("OutlTxt"))
    _text_block(outl, "TextOutlTxt", pos.short_text)
    if pos.long_text:
        detail = ET.SubElement(complete, _q("DetailTxt"))
        _text_block(detail, "Text", pos.long_text)

    if pos.unit_price is not None:
        ET.SubElement(item, _q("UP")).text = _fmt_money(pos.unit_price)
        total = pos.item_total
        if total is not None:
            ET.SubElement(item, _q("IT")).text = _fmt_money(total)


def build_tree(boq: BillOfQuantities, *, generated_at: datetime, prog_system: str) -> ET.Element:
    """Build the GAEB element tree. `generated_at` is injected (not read from the clock) so the
    output is deterministic for tests and reproducible for a given input."""
    if not boq.is_priced:
        raise UnpricedOfferError(
            "a .x84 offer needs a unit price on every position; some are missing"
        )

    ET.register_namespace("", GAEB_NAMESPACE)
    root = ET.Element(_q("GAEB"))

    info = ET.SubElement(root, _q("GAEBInfo"))
    ET.SubElement(info, _q("Version")).text = GAEB_VERSION
    ET.SubElement(info, _q("VersDate")).text = GAEB_VERSDATE
    ET.SubElement(info, _q("Date")).text = generated_at.strftime("%Y-%m-%d")
    ET.SubElement(info, _q("Time")).text = generated_at.strftime("%H:%M:%S")
    ET.SubElement(info, _q("ProgSystem")).text = prog_system

    prj = ET.SubElement(root, _q("PrjInfo"))
    ET.SubElement(prj, _q("NamePrj")).text = boq.project_name
    ET.SubElement(prj, _q("Cur")).text = boq.currency

    award = ET.SubElement(root, _q("Award"))
    ET.SubElement(award, _q("DP")).text = DATA_PHASE_OFFER
    ET.SubElement(award, _q("Cur")).text = boq.currency

    boq_el = ET.SubElement(award, _q("BoQ"))
    boq_info = ET.SubElement(boq_el, _q("BoQInfo"))
    ET.SubElement(boq_info, _q("Name")).text = boq.name or boq.project_name
    body = ET.SubElement(boq_el, _q("BoQBody"))

    # One category level: if positions are grouped by section, each section is a BoQCtgy; if every
    # position is ungrouped (section ""), items go straight into a single Itemlist.
    sections = boq.sections
    flat = sections == ("",)
    if flat:
        itemlist = ET.SubElement(body, _q("Itemlist"))
        for pos in boq.positions:
            _item_element(itemlist, pos)
    else:
        for section in sections:
            ctgy = ET.SubElement(body, _q("BoQCtgy"))
            ctgy.set("ID", str(uuid.uuid5(_ID_NAMESPACE, f"ctgy:{section}")))
            _text_block(ctgy, "LblTx", section)
            ctgy_body = ET.SubElement(ctgy, _q("BoQBody"))
            itemlist = ET.SubElement(ctgy_body, _q("Itemlist"))
            for pos in boq.positions:
                if pos.section == section:
                    _item_element(itemlist, pos)

    totals = ET.SubElement(boq_el, _q("Totals"))
    ET.SubElement(totals, _q("Total")).text = _fmt_money(boq.total)

    return root


def write_x84(
    boq: BillOfQuantities,
    *,
    generated_at: datetime,
    prog_system: str = "LuxMedicine GAEB export",
) -> bytes:
    """Serialise `boq` to GAEB DA XML `.x84` bytes (UTF-8, with declaration). Raises
    `UnpricedOfferError` if any position lacks a price."""
    root = build_tree(boq, generated_at=generated_at, prog_system=prog_system)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
