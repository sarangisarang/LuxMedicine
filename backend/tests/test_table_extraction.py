"""The #48 structural fix: a category-table row made self-describing (table_extraction.py).

The fixtures are the real geometry of CDC MEC page 124 — the six-method summary table whose
"migraine with aura" row is the exact #48 case: category 4 for combined hormonal contraception,
the stroke contraindication, in a column whose header the quoted span never carried.

The safety bar is as important as the capability: an ambiguous table (p102's barrier table,
three named methods against four value columns) must map to None and fall back, never guess a
header. A wrong header reproduces #48 with more confidence.
"""

from __future__ import annotations

from app.services.table_extraction import (
    MethodColumn,
    describe_row,
    find_method_columns,
    map_row,
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
