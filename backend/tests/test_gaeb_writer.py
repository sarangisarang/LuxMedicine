"""The .x84 writer: the XML must be well-formed, phased as an offer, and arithmetically honest —
every IT is Qty×UP and the grand Total is the sum of them. These are the properties a construction
program checks when it imports the bid, so they are what the tests assert."""

import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

import pytest

from app.services.gaeb.model import BillOfQuantities, Position
from app.services.gaeb.writer import (
    DATA_PHASE_OFFER,
    GAEB_NAMESPACE,
    UnpricedOfferError,
    write_x84,
)

AT = datetime(2026, 7, 25, 12, 0, 0)
NS = {"g": GAEB_NAMESPACE}


def _boq(**kw) -> BillOfQuantities:
    positions = kw.pop(
        "positions",
        (
            Position(oz="01.0010", short_text="Beton C25/30", quantity=Decimal("10"), unit="m3",
                     unit_price=Decimal("125.50")),
            Position(oz="01.0020", short_text="Bewehrungsstahl", quantity=Decimal("2.5"), unit="t",
                     unit_price=Decimal("900.00")),
        ),
    )
    return BillOfQuantities(project_name="Neubau Halle", positions=positions, **kw)


def _root(boq: BillOfQuantities) -> ET.Element:
    return ET.fromstring(write_x84(boq, generated_at=AT))


def test_output_is_well_formed_and_in_the_gaeb_namespace():
    root = _root(_boq())
    assert root.tag == f"{{{GAEB_NAMESPACE}}}GAEB"


def test_it_is_an_offer_phase_84():
    root = _root(_boq())
    assert root.find("./g:Award/g:DP", NS).text == DATA_PHASE_OFFER == "84"


def test_every_position_becomes_an_item_with_qty_unit_and_price():
    root = _root(_boq())
    items = root.findall(".//g:Item", NS)
    assert len(items) == 2
    first = items[0]
    assert first.find("g:Qty", NS).text == "10.000"
    assert first.find("g:QU", NS).text == "m3"
    assert first.find("g:UP", NS).text == "125.50"


def test_item_total_is_quantity_times_unit_price():
    root = _root(_boq())
    # 2.5 t × 900.00 = 2250.00
    second = root.findall(".//g:Item", NS)[1]
    assert second.find("g:IT", NS).text == "2250.00"


def test_grand_total_is_the_sum_of_item_totals():
    root = _root(_boq())
    # 10×125.50 = 1255.00  +  2.5×900 = 2250.00  =>  3505.00
    assert root.find(".//g:Totals/g:Total", NS).text == "3505.00"


def test_rounding_is_to_the_cent_half_up():
    boq = _boq(positions=(
        Position(oz="1", short_text="x", quantity=Decimal("3"), unit="St",
                 unit_price=Decimal("1.005")),  # 3 × 1.005 = 3.015 -> 3.02
    ))
    assert _root(boq).find(".//g:Item/g:IT", NS).text == "3.02"


def test_sections_become_categories_and_flat_lists_do_not():
    flat = _root(_boq())
    assert flat.findall(".//g:BoQCtgy", NS) == []

    grouped = _root(_boq(positions=(
        Position(oz="1.1", short_text="a", quantity=Decimal("1"), unit="St",
                 unit_price=Decimal("1"), section="Erdarbeiten"),
        Position(oz="2.1", short_text="b", quantity=Decimal("1"), unit="St",
                 unit_price=Decimal("2"), section="Maurerarbeiten"),
    )))
    ctgys = grouped.findall(".//g:BoQCtgy", NS)
    assert len(ctgys) == 2
    labels = [c.find("g:LblTx/g:p/g:span", NS).text for c in ctgys]
    assert labels == ["Erdarbeiten", "Maurerarbeiten"]


def test_long_text_is_emitted_only_when_present():
    with_long = _root(_boq(positions=(
        Position(oz="1", short_text="short", long_text="the full spec", quantity=Decimal("1"),
                 unit="St", unit_price=Decimal("1")),
    )))
    assert with_long.find(".//g:DetailTxt/g:Text/g:p/g:span", NS).text == "the full spec"
    assert with_long.find(".//g:OutlTxt/g:TextOutlTxt/g:p/g:span", NS).text == "short"


def test_currency_propagates():
    root = _root(_boq(currency="CHF"))
    assert root.find("./g:Award/g:Cur", NS).text == "CHF"


def test_an_unpriced_position_refuses_to_export():
    boq = _boq(positions=(
        Position(oz="1", short_text="no price", quantity=Decimal("1"), unit="St"),
    ))
    with pytest.raises(UnpricedOfferError):
        write_x84(boq, generated_at=AT)


def test_output_is_deterministic():
    boq = _boq()
    assert write_x84(boq, generated_at=AT) == write_x84(boq, generated_at=AT)
