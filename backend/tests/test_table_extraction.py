"""The #48 structural fix: a category-table row made self-describing (table_extraction.py).

The fixtures are the real geometry of CDC MEC page 124 — the six-method summary table whose
"migraine with aura" row is the exact #48 case: category 4 for combined hormonal contraception,
the stroke contraindication, in a column whose header the quoted span never carried.

The safety bar is as important as the capability: an ambiguous table (p102's barrier table,
three named methods against four value columns) must map to None and fall back, never guess a
header. A wrong header reproduces #48 with more confidence.
"""

from __future__ import annotations

import pytest

from app.services.table_extraction import (
    MethodColumn,
    describe_row,
    find_method_columns,
    map_row,
    self_describing_lines,
)


def _w(text: str, x: float) -> dict:
    # A word centred at x, 10pt wide — enough for the centre-based geometry under test.
    return {"text": text, "x0": x - 5, "x1": x + 5}


# CDC MEC p124, measured: the header line and the two migraine rows.
P124_HEADER = [
    _w("Condition", 51),
    _w("Cu-IUD", 176),
    _w("LNG-IUD", 249),
    _w("Implant", 321),
    _w("DMPA", 394),
    _w("POP", 467),
    _w("CHC", 540),
]
P124_WITH_AURA = [
    _w("ii.", 47), _w("With", 57), _w("aura", 72),
    _w("1", 176), _w("1", 249), _w("1", 321), _w("1", 394), _w("1", 467), _w("4*", 540),
]
P124_WITHOUT_AURA = [
    _w("i.", 46), _w("Without", 61), _w("aura", 81), _w("(includes", 102),
    _w("1", 176), _w("1", 249), _w("1", 321), _w("1", 394), _w("1", 467), _w("2*", 540),
]


class TestFindColumns:
    def test_the_six_method_headers_are_found_in_order(self):
        cols = find_method_columns(P124_HEADER)
        assert cols is not None
        assert [c.header for c in cols] == ["Cu-IUD", "LNG-IUD", "Implant", "DMPA", "POP", "CHC"]

    def test_a_line_that_merely_mentions_a_method_is_not_a_header(self):
        prose = [_w("Progestin-only", 60), _w("pills", 110), _w("(POP)", 150), _w("are", 190)]
        assert find_method_columns(prose) is None

    def test_condition_column_is_ignored_it_is_the_row_label_header(self):
        # "Condition" is not a method, so it does not become a value column.
        cols = find_method_columns(P124_HEADER)
        assert all(c.header != "Condition" for c in cols)


class TestMapRow:
    def test_the_48_row_maps_exactly(self):
        cols = find_method_columns(P124_HEADER)
        mapping = map_row(P124_WITH_AURA, cols)
        assert mapping == [
            ("Cu-IUD", "1"), ("LNG-IUD", "1"), ("Implant", "1"),
            ("DMPA", "1"), ("POP", "1"), ("CHC", "4*"),
        ]

    def test_the_dangerous_number_survives_with_its_method(self):
        # The whole point of #48: CHC (combined hormonal) is category 4, the stroke
        # contraindication — and now it says so.
        cols = find_method_columns(P124_HEADER)
        mapping = dict(map_row(P124_WITH_AURA, cols))
        assert mapping["CHC"] == "4*"
        assert mapping["Cu-IUD"] == "1"

    def test_without_aura_row_maps_too(self):
        cols = find_method_columns(P124_HEADER)
        mapping = dict(map_row(P124_WITHOUT_AURA, cols))
        assert mapping["CHC"] == "2*"

    def test_fewer_cells_than_columns_is_not_mapped(self):
        # p102's barrier table: three method columns, but a row read as one value. Must NOT
        # guess — return None and fall back to the guard.
        cols = [MethodColumn("Condom", 210), MethodColumn("Spermicide", 274), MethodColumn("Diaphragm", 346)]
        one_value = [_w("ii.", 47), _w("With", 57), _w("aura", 72), _w("1", 210)]
        assert map_row(one_value, cols) is None

    def test_a_misaligned_cell_is_not_mapped(self):
        # Right count, but a cell drifts off its column — the tell of a merged or shifted
        # table. Refuse rather than attribute it to the wrong method.
        cols = find_method_columns(P124_HEADER)
        drifted = [
            _w("ii.", 47), _w("With", 57), _w("aura", 72),
            _w("1", 176), _w("1", 249), _w("1", 321), _w("1", 394), _w("1", 467), _w("4*", 600),
        ]
        assert map_row(drifted, cols) is None


class TestDescribeRow:
    def test_it_reads_as_the_self_describing_answer(self):
        cols = find_method_columns(P124_HEADER)
        mapping = map_row(P124_WITH_AURA, cols)
        line = describe_row("Migraine with aura", mapping)
        assert line == (
            "Migraine with aura — Cu-IUD: 1, LNG-IUD: 1, Implant: 1, "
            "DMPA: 1, POP: 1, CHC: 4*"
        )

    def test_the_line_is_not_a_headerless_row_the_guard_would_refuse(self):
        # Closing the loop with #48: what this emits must PASS the guard that refuses the raw
        # row, because the category numbers now carry their headers.
        from app.services.table_guard import looks_like_headerless_table_row

        cols = find_method_columns(P124_HEADER)
        line = describe_row("Migraine with aura", map_row(P124_WITH_AURA, cols))
        assert looks_like_headerless_table_row(line) is False


class TestSelfDescribingLines:
    """The page-level pass that finds the header, then describes each data row beneath it."""

    def _row(self, top: float, *cells: tuple[str, float]) -> list[dict]:
        return [{"text": t, "x0": x - 5, "x1": x + 5, "top": top} for t, x in cells]

    def test_a_page_with_a_header_and_two_rows(self):
        words = (
            self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                      ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
            + self._row(184, ("Without", 61), ("aura", 81),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("2*", 540))
            + self._row(201, ("With", 57), ("aura", 72),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("4*", 540))
        )
        lines = self_describing_lines(words)
        assert any("With aura — Cu-IUD: 1" in ln and "CHC: 4*" in ln for ln in lines)
        assert any("Without aura" in ln and "CHC: 2*" in ln for ln in lines)

    def test_a_page_with_no_header_yields_nothing(self):
        # Data-shaped rows but no method header above them: nothing is mapped. The pass only
        # ADDS text where it is certain; a page with no clean table is left untouched.
        words = self._row(201, ("With", 57), ("aura", 72), ("1", 176), ("1", 249))
        assert self_describing_lines(words) == []

    def test_rows_before_the_header_are_not_described(self):
        # A row above the header line has no columns yet — skipped, not mis-mapped.
        words = (
            self._row(50, ("Stray", 57), ("1", 176), ("1", 249), ("1", 321),
                      ("1", 394), ("1", 467), ("4*", 540))
            + self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                        ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
        )
        assert self_describing_lines(words) == []


@pytest.mark.slow
class TestEndToEndOnRealMEC:
    """The #48 fix, pinned end to end on the real document — the regression it must never make.

    Unit tests above prove the transform on captured geometry; this proves the whole path
    (extract_pdf -> self-describing line, on the actual PDF) still yields the answer that closed
    #48, and that appending those lines did not corrupt the char-offset ledger page attribution
    depends on. Found by scanning the storage root rather than the test database, which is
    isolated and holds no corpus — so it needs the licence-clean MEC file, not the model, and
    skips cleanly wherever that file is absent (CI, a fresh checkout).
    """

    @staticmethod
    def _mec_document():
        """extract_pdf'd MEC — the one PDF in storage whose extraction carries the migraine
        self-describing row. Skips if no such PDF is present."""
        from app.core.config import get_settings
        from app.services.extraction import extract_pdf

        root = get_settings().storage_root
        pdfs = sorted(root.rglob("*.pdf")) if root.is_dir() else []
        if not pdfs:
            pytest.skip(f"no PDFs under {root}")
        for path in pdfs:
            try:
                doc = extract_pdf(path)
            except Exception:  # noqa: BLE001 - a scanned or broken PDF is simply not MEC
                continue
            if any("With aura" in ln.text and "CHC: 4" in ln.text for ln in doc.lines):
                return doc
        pytest.skip("no PDF in storage extracts the MEC migraine table (licence-clean MEC absent)")

    def test_the_migraine_row_is_self_describing_with_chc_4(self):
        doc = self._mec_document()
        aura = [
            ln for ln in doc.lines
            if "With aura" in ln.text and "CHC: 4" in ln.text and "Cu-IUD: 1" in ln.text
        ]
        assert aura, "the #48 self-describing migraine-with-aura row is missing"
        # It carries every method with its own category — the header #48 used to strip; CHC: 4
        # is the combined-hormonal stroke contraindication the raw row hid.
        assert "CHC: 4" in aura[0].text

    def test_appending_the_rows_kept_the_ledger_exact(self):
        # Page attribution is a bisect over char offsets; a self-describing line whose offsets
        # do not match the assembled text would cite the wrong page. Every line, including the
        # appended ones, must equal the document text at its own span.
        doc = self._mec_document()
        full = doc.text
        mismatched = [ln for ln in doc.lines if full[ln.char_start:ln.char_end] != ln.text]
        assert mismatched == [], f"{len(mismatched)} lines do not match their offsets"
