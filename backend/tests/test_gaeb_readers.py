"""Readers turn a source file into positions. The subtle part is numbers (German vs English) and
finding the header row; the strongest guarantee is the GAEB round-trip — what the writer emits, the
reader reads back unchanged."""

import io
from datetime import datetime
from decimal import Decimal

import openpyxl
import pytest

from app.services.gaeb.model import BillOfQuantities, Position
from app.services.gaeb.readers import (
    NoPositionsError,
    UnreadableFileError,
    parse_decimal,
    read_csv,
    read_gaeb,
    read_xlsx,
)
from app.services.gaeb.writer import write_x84


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.234,56", Decimal("1234.56")),   # German
        ("1,234.56", Decimal("1234.56")),   # English
        ("1234,56", Decimal("1234.56")),    # comma decimal, no thousands
        ("1234.56", Decimal("1234.56")),    # plain
        ("€ 125,50", Decimal("125.50")),    # currency symbol
        ("10", Decimal("10")),
        ("", None),
        (None, None),
    ],
)
def test_parse_decimal_reads_both_conventions(raw, expected):
    assert parse_decimal(raw) == expected


def test_parse_decimal_rejects_nonsense():
    with pytest.raises(UnreadableFileError):
        parse_decimal("not a number")


CSV_DE = (
    "Pos;Kurztext;Menge;Einheit;Einheitspreis\n"
    "01.0010;Beton C25/30;10;m3;125,50\n"
    "01.0020;Bewehrungsstahl;2,5;t;900,00\n"
)


def test_read_csv_german_semicolons():
    boq = read_csv(CSV_DE.encode("utf-8"), project_name="Halle")
    assert boq.project_name == "Halle"
    assert len(boq.positions) == 2
    p = boq.positions[0]
    assert (p.oz, p.short_text, p.unit) == ("01.0010", "Beton C25/30", "m3")
    assert p.quantity == Decimal("10")
    assert p.unit_price == Decimal("125.50")
    assert boq.positions[1].quantity == Decimal("2.5")


def test_read_csv_empty_is_unreadable():
    with pytest.raises(UnreadableFileError):
        read_csv(b"   \n  \n", project_name="x")


def test_read_csv_unknown_columns_has_no_positions():
    with pytest.raises(NoPositionsError):
        read_csv(b"Alpha;Beta;Gamma\n1;2;3\n", project_name="x")


def _xlsx(rows: list[list[object]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_read_xlsx_skips_title_rows_above_the_header():
    data = _xlsx([
        ["Leistungsverzeichnis Neubau"],          # a title row, no table
        [],                                        # a blank row
        ["OZ", "Bezeichnung", "Menge", "ME", "EP"],
        ["1.1", "Mauerwerk", 12.5, "m2", 45.0],
        ["1.2", "Putz", 30, "m2", 18.5],
    ])
    boq = read_xlsx(data, project_name="Neubau")
    assert len(boq.positions) == 2
    assert boq.positions[0].short_text == "Mauerwerk"
    assert boq.positions[0].quantity == Decimal("12.5")
    assert boq.positions[1].unit_price == Decimal("18.5")


def test_read_xlsx_without_a_recognisable_header_raises():
    data = _xlsx([["foo", "bar"], [1, 2]])
    with pytest.raises(NoPositionsError):
        read_xlsx(data, project_name="x")


def test_gaeb_round_trip_preserves_positions():
    boq = BillOfQuantities(
        project_name="Rathaus",
        positions=(
            Position(oz="01.0010", short_text="Beton", quantity=Decimal("10"), unit="m3",
                     unit_price=Decimal("125.50"), section="Rohbau"),
            Position(oz="01.0020", short_text="Stahl", quantity=Decimal("2.5"), unit="t",
                     unit_price=Decimal("900.00"), section="Rohbau"),
        ),
    )
    xml = write_x84(boq, generated_at=datetime(2026, 7, 25, 12, 0, 0))

    back = read_gaeb(xml)
    assert back.project_name == "Rathaus"
    assert len(back.positions) == 2
    a = back.positions[0]
    assert a.oz == "01.0010"
    assert a.short_text == "Beton"
    assert a.quantity == Decimal("10")
    assert a.unit == "m3"
    assert a.unit_price == Decimal("125.50")
    assert a.section == "Rohbau"


def test_read_gaeb_rejects_non_gaeb_xml():
    with pytest.raises(UnreadableFileError):
        read_gaeb(b"<html><body>not gaeb</body></html>")
