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

    def test_the_parent_condition_is_carried_into_an_enumerated_sub_row(self):
        """The live prod bug: the migraine-with-aura row embedded one rank below the passage
        window because its label was "ii. With aura" — the word "migraine" lives on the
        "b. Migraine" line above, which the raw sub-row drops. Real CDC MEC p124 geometry:
        a bare "b. Migraine" heading, then the two enumerated sub-rows with cells."""
        words = (
            self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                      ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
            + self._row(158, ("Headaches", 60))
            + self._row(167, ("a.", 47), ("Nonmigraine", 80), ("(mild", 120), ("or", 145), ("severe)", 170),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("1*", 540))
            + self._row(175, ("b.", 47), ("Migraine", 80))
            + self._row(184, ("i.", 46), ("Without", 61), ("aura", 81),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("2*", 540))
            + self._row(201, ("ii.", 47), ("With", 57), ("aura", 72),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("4*", 540))
        )
        lines = self_describing_lines(words)

        # The row that answers the question now carries "Migraine" and still names CHC: 4.
        aura = [ln for ln in lines if "With aura" in ln]
        assert aura, "no with-aura row emitted"
        assert aura[0].startswith("Migraine — With aura —"), aura[0]
        assert "CHC: 4*" in aura[0]

        # And the without-aura sub-row inherits the same parent, not the wrong one.
        without = [ln for ln in lines if "Without aura" in ln]
        assert without and without[0].startswith("Migraine — Without aura")

    def test_a_lowercase_continuation_line_never_becomes_the_parent(self):
        """"i. Without aura (includes / menstrual migraine)" wraps across two visual rows; the
        lowercase second line must not be captured as the parent of "ii. With aura"."""
        words = (
            self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                      ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
            + self._row(175, ("b.", 47), ("Migraine", 80))
            + self._row(184, ("i.", 46), ("Without", 61), ("aura", 81), ("(includes", 120),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("2*", 540))
            + self._row(192, ("menstrual", 61), ("migraine)", 100))  # lowercase wrap, no cells
            + self._row(201, ("ii.", 47), ("With", 57), ("aura", 72),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("4*", 540))
        )
        aura = [ln for ln in self_describing_lines(words) if "With aura" in ln]
        assert aura and aura[0].startswith("Migraine — With aura —"), aura

    def test_a_sibling_row_whose_cells_were_refused_never_becomes_the_parent(self):
        """The regression that put 13 mislabelled conditions into the corpus.

        "a. Uncomplicated" carries seven cells against six columns, so map_row correctly
        refuses it — and the first version of the parent rule read that refusal as "this is a
        heading", making the next sibling read "Uncomplicated — Complicated (pulmonary …".
        Measured on the real MEC that also produced "Compensated (normal liver — Decompensated
        (impaired" and "<6 months — ≥6 months": siblings presented as parent and child, with
        correct categories under a wrong condition name.
        """
        words = (
            self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                      ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
            # seven cells, six columns -> map_row refuses; this row is a SIBLING, not a heading
            + self._row(150, ("a.", 47), ("Uncomplicated", 90),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("1", 500), ("2*", 540))
            + self._row(167, ("b.", 47), ("Complicated", 90),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("4*", 540))
        )
        lines = self_describing_lines(words)
        complicated = [ln for ln in lines if "Complicated" in ln]

        assert complicated, "the mappable sibling was not emitted at all"
        assert not complicated[0].startswith("Uncomplicated —"), (
            f"a refused sibling became the parent: {complicated[0]!r}"
        )

    def test_a_refused_sibling_does_not_steal_the_real_parent(self):
        """The other direction. A sibling whose mapping fails must not *clear* the heading
        either — the row after it is still that condition's sub-row."""
        words = (
            self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                      ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
            + self._row(175, ("b.", 47), ("Migraine", 80))          # real heading, no cells
            + self._row(184, ("i.", 46), ("Without", 61), ("aura", 81),
                        ("1", 176), ("1", 249), ("1", 500))          # refused: 3 cells, 6 columns
            + self._row(201, ("ii.", 47), ("With", 57), ("aura", 72),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("4*", 540))
        )
        aura = [ln for ln in self_describing_lines(words) if "With aura" in ln]
        assert aura and aura[0].startswith("Migraine — With aura —"), aura

    def test_a_label_cut_where_its_cell_wrapped_is_completed_from_the_next_row(self):
        """_row_label reads one geometric row, so a cell that wraps loses its tail. Measured on
        the real MEC: 32 labels carried an unclosed bracket and 59 ended on a word no phrase
        ends on — including "ii. Systolic ≥160 mm Hg or", which drops the diastolic half of a
        blood-pressure threshold."""
        words = (
            self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                      ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
            + self._row(150, ("d.", 47), ("Family", 70), ("history", 95), ("(first-degree", 130),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("2", 540))
            + self._row(158, ("relatives)", 70))  # the wrapped tail, in the label region
        )
        lines = self_describing_lines(words)
        assert lines and "Family history (first-degree relatives)" in lines[0], lines

    def test_a_complete_label_is_never_extended(self):
        """The safety property that makes this fix cheap: it acts only where a label already
        reads as broken, so the several hundred intact labels cannot regress. Measured on the
        real MEC — 51 truncated segments repaired, 0 complete segments altered."""
        words = (
            self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                      ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
            + self._row(150, ("a.", 47), ("Varicose", 80), ("veins", 115),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("1", 540))
            + self._row(158, ("Superficial", 80), ("thrombosis", 130))  # a *different* condition
        )
        lines = self_describing_lines(words)
        assert lines and lines[0].startswith("a. Varicose veins — Cu-IUD: 1"), lines
        assert "Superficial" not in lines[0]

    def test_the_comment_column_is_never_absorbed_into_a_label(self):
        """A wrapped comment sits to the RIGHT of the value columns; only the label region is
        read, so clarification prose can never be pulled into the condition name."""
        words = (
            self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                      ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
            + self._row(150, ("i.", 47), ("With", 70), ("risk", 95), ("factors", 120), ("for", 150),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("2", 540))
            + self._row(158, ("VTE", 70), ("(e.g.,", 100), ("age)", 130),
                        ("these", 620), ("factors", 660), ("might", 700))  # comment column
        )
        line = self_describing_lines(words)[0]
        assert "VTE (e.g., age)" in line
        assert "these" not in line and "might" not in line

    def test_completion_stops_at_the_next_data_row(self):
        """A following row carrying category cells is the next condition, not a wrapped tail —
        absorbing it would name one condition with the next one's words."""
        words = (
            self._row(89, ("Condition", 51), ("Cu-IUD", 176), ("LNG-IUD", 249),
                      ("Implant", 321), ("DMPA", 394), ("POP", 467), ("CHC", 540))
            + self._row(150, ("a.", 47), ("Thrombophilia", 80), ("(e.g.,", 140),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("2", 540))
            + self._row(158, ("b.", 47), ("Sickle", 80), ("cell", 110),
                        ("1", 176), ("1", 249), ("1", 321), ("1", 394), ("1", 467), ("2", 540))
        )
        lines = self_describing_lines(words)
        thrombo = [ln for ln in lines if "Thrombophilia" in ln]
        assert thrombo and "Sickle" not in thrombo[0], thrombo

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
    def _mec_path():
        """The MEC PDF's path, found by probing first pages rather than extracting each file.

        The storage root also holds several 1000-page textbooks; `_mec_document` below runs a
        full extract_pdf per candidate, which is fine once but far too slow for a scan that
        opens the document itself. A two-page probe identifies MEC in milliseconds.
        """
        import pdfplumber

        from app.core.config import get_settings

        root = get_settings().storage_root
        for path in sorted(root.rglob("*.pdf")) if root.is_dir() else []:
            try:
                with pdfplumber.open(path) as pdf:
                    head = " ".join((p.extract_text() or "") for p in pdf.pages[:2])
            except Exception:  # noqa: BLE001 - a scanned or broken PDF is simply not MEC
                continue
            if "Medical Eligibility Criteria" in head:
                return path
        pytest.skip("licence-clean CDC MEC is not in the storage root")

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

    def test_no_emitted_row_takes_its_parent_from_a_row_that_has_categories(self):
        """The structural invariant, over the whole real document rather than a fixture.

        The unit tests above pin the shape on captured geometry; this asserts the property that
        actually failed in production — across every table on every page, no emitted line may
        have inherited its condition from a row carrying category cells. That is the difference
        between a heading and a sibling, and conflating them is what shipped "Compensated
        (normal liver — Decompensated (impaired" to a clinical corpus.

        Written as an invariant rather than a list of known-bad rows because the 13 were found
        by reading the corpus, not by any test — a count would only re-check the ones already
        known, which is precisely how this class stayed invisible.

        **This drives the shipped function.** The first version of this test re-implemented the
        corrected rule inline and then checked its own re-implementation — a tautology that
        passed even with the guard deleted from the real code, which a mutation run caught. The
        two sides here are independent: the lines come from `self_describing_lines` itself,
        while the set of sibling labels is derived from a property (this row carries category
        cells and map_row refused it) that says nothing about how parents are chosen.
        """
        import pdfplumber

        from app.services.table_extraction import (
            _CATEGORY_CELL,
            _row_label,
            _rows,
            _strip_enumerator,
            find_method_columns,
            map_row,
        )

        # Compared per page, because self_describing_lines resolves parents per page and a
        # label is only evidence about its own. Collecting siblings document-wide flagged 13
        # correct rows — "Confirmed gestational — Persistently elevated β-hCG" is a real
        # parent and child on p56, whose parent text merely also appears as a refused data row
        # elsewhere. Measured: 13 offenders scoped globally, 0 scoped to the page.
        offenders: list[str] = []

        with pdfplumber.open(self._mec_path()) as pdf:
            for page in pdf.pages:
                words = page.extract_words()
                emitted = self_describing_lines(words)  # the shipped behaviour

                # Independently: which labels on THIS page belong to data rows, not headings?
                sibling_labels: set[str] = set()
                rows = _rows(words)
                columns = None
                for row in rows:
                    header = find_method_columns(row)
                    if header is not None:
                        columns = header
                        continue
                    if columns is None:
                        continue
                    if map_row(row, columns) is None and any(
                        _CATEGORY_CELL.match(w["text"]) for w in row
                    ):
                        label = _strip_enumerator(_row_label(row, columns))
                        if label:
                            sibling_labels.add(label)

                offenders.extend(
                    line
                    for line in emitted
                    if len(line.split(" — ")) >= 3 and line.split(" — ")[0] in sibling_labels
                )
                page.flush_cache()

        assert offenders == [], (
            f"{len(offenders)} row(s) inherited a condition from a row that carries category "
            f"cells — a sibling presented as a parent. First: {offenders[:3]}"
        )

    def test_the_blood_pressure_threshold_keeps_both_halves(self):
        """The clinically sharpest instance of the truncation bug, pinned on the real document.

        The row read "ii. Systolic ≥160 mm Hg or — …, CHC: 4" — cut exactly where the printed
        cell wrapped, dropping "diastolic ≥100 mm Hg". A clinician reading it sees a threshold
        conditioned on systolic alone, quoted verbatim and cited correctly. Half a threshold is
        not a smaller version of a threshold.
        """
        import pdfplumber

        found: list[str] = []
        with pdfplumber.open(self._mec_path()) as pdf:
            for page in pdf.pages:
                found.extend(
                    line
                    for line in self_describing_lines(page.extract_words())
                    if "Systolic ≥160" in line
                )
                page.flush_cache()

        assert found, "the ≥160 mm Hg row is not emitted at all"
        for line in found:
            assert "diastolic ≥100 mm Hg" in line, f"threshold still cut in half: {line[:90]!r}"

    def test_appending_the_rows_kept_the_ledger_exact(self):
        # Page attribution is a bisect over char offsets; a self-describing line whose offsets
        # do not match the assembled text would cite the wrong page. Every line, including the
        # appended ones, must equal the document text at its own span.
        doc = self._mec_document()
        full = doc.text
        mismatched = [ln for ln in doc.lines if full[ln.char_start:ln.char_end] != ln.text]
        assert mismatched == [], f"{len(mismatched)} lines do not match their offsets"
