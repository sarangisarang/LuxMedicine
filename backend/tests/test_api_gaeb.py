"""The GAEB endpoints, end to end through the app: an upload parses to positions, and confirmed
positions export to a downloadable .x84. Auth is stubbed the same way the translation test does it;
no database is touched, so these run without the pg fixtures."""

import xml.etree.ElementTree as ET

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.gaeb.writer import GAEB_NAMESPACE

pytestmark = pytest.mark.asyncio

CSV_DE = (
    "Pos;Kurztext;Menge;Einheit;Einheitspreis\n"
    "01.0010;Beton C25/30;10;m3;125,50\n"
    "01.0020;Bewehrungsstahl;2,5;t;900,00\n"
).encode("utf-8")


def _client() -> AsyncClient:
    from app.core.auth import Clinician, current_clinician
    from app.main import app

    app.dependency_overrides[current_clinician] = lambda: Clinician(
        actor_id="dr-001", clinic_id="clinic-demo"
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _clear() -> None:
    from app.main import app

    app.dependency_overrides.clear()


async def test_parse_reads_a_csv_into_positions():
    try:
        async with _client() as client:
            r = await client.post(
                "/gaeb/parse",
                files={"file": ("lv.csv", CSV_DE, "text/csv")},
                data={"project_name": "Halle"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["project_name"] == "Halle"
        assert len(body["positions"]) == 2
        first = body["positions"][0]
        assert first["short_text"] == "Beton C25/30"
        # Verbatim: the price is handed back exactly as the file wrote it ("125,50"), not reformatted.
        assert first["unit_price"] == "125,50"
        assert first["quantity"] == "10"
    finally:
        await _clear()


async def test_parse_unreadable_pdf_is_422():
    # PDF is supported now, but this is not a valid PDF — a clear 422, not a crash or a garbled parse.
    try:
        async with _client() as client:
            r = await client.post(
                "/gaeb/parse",
                files={"file": ("lv.pdf", b"%PDF-1.4\nnot really a pdf", "application/pdf")},
            )
        assert r.status_code == 422
    finally:
        await _clear()


async def test_parse_empty_file_is_422():
    try:
        async with _client() as client:
            r = await client.post("/gaeb/parse", files={"file": ("empty.csv", b"", "text/csv")})
        assert r.status_code == 422
    finally:
        await _clear()


async def test_export_returns_a_downloadable_x84():
    body = {
        "project_name": "Neubau Halle",
        "positions": [
            {"oz": "01.0010", "short_text": "Beton", "quantity": "10", "unit": "m3", "unit_price": "125.50"},
            {"oz": "01.0020", "short_text": "Stahl", "quantity": "2.5", "unit": "t", "unit_price": "900.00"},
        ],
    }
    try:
        async with _client() as client:
            r = await client.post("/gaeb/export", json=body)
        assert r.status_code == 200, r.text
        assert r.headers["content-disposition"] == 'attachment; filename="Neubau_Halle.x84"'
        root = ET.fromstring(r.content)
        ns = {"g": GAEB_NAMESPACE}
        assert root.find("./g:Award/g:DP", ns).text == "84"
        assert root.find(".//g:Totals/g:Total", ns).text == "3505.00"
    finally:
        await _clear()


async def test_export_refuses_an_unpriced_position():
    body = {
        "project_name": "x",
        "positions": [{"oz": "1", "short_text": "no price", "quantity": "1", "unit": "St"}],
    }
    try:
        async with _client() as client:
            r = await client.post("/gaeb/export", json=body)
        assert r.status_code == 422
    finally:
        await _clear()
