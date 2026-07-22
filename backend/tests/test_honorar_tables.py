"""The HOAI Honorartafel structural fix (honorar_tables.py) — #48 for fees.

The danger is a fee row quoted without its Honorarzone: "500 000 45 232 53 006 …" is a real
grid of euros the guard refuses, because the number that says which zone owns which fee is a
column heading the span never carries. This makes the row self-describing so it can be quoted
whole, and pins the two properties that make that safe: the von/bis split is correct, and an
ambiguous row is skipped rather than guessed.

The fixtures are extract_tables-shaped (a header row, then data rows whose cells stack their
sub-rows with newlines), copied from the real official HOAI structure — the 5-zone § 35
Anrechenbare-Kosten table and the 3-zone § 20 Fläche table.
"""

from __future__ import annotations

from app.services.honorar_tables import self_describing_honorar_rows, von_bis
from app.services.table_guard import looks_like_headerless_table_row


class TestVonBis:
    def test_a_clean_pair_splits_by_digit_groups(self):
        assert von_bis("70 439 85 269") == ("70 439", "85 269")

    def test_a_six_figure_pair_splits(self):
        assert von_bis("155 461 188 190") == ("155 461", "188 190")

    def test_an_odd_number_of_groups_is_refused(self):
        # Would need guessing where von ends — the exact case that must never be attributed.
        assert von_bis("999 500 1 001 000") is None  # 5 groups
        assert von_bis("34 865") is None  # a lone number, not a pair

    def test_von_below_bis_is_enforced(self):
        # A split that puts the larger number first is a mis-split; refuse rather than emit a
        # backwards range that would read as a wrong Mindestsatz.
        assert von_bis("85 269 70 439") is None

    def test_empty_and_nonnumeric_are_refused(self):
        assert von_bis("") is None
        assert von_bis("von bis") is None


class TestSelfDescribingHonorarRows:
    # The § 35 five-zone table, exactly as extract_tables returns it: header row, then one data
    # row whose cells stack five anrechenbare-Kosten sub-rows with newlines.
    FIVE_ZONE = [
        [
            "Anrechenbare\nKosten\nin Euro",
            "Honorarzone I\nsehr geringe\nvon bis\nEuro",
            "Honorarzone II\ngeringe\nvon bis\nEuro",
            "Honorarzone III\ndurchschnittliche\nvon bis\nEuro",
            "Honorarzone IV\nhohe\nvon bis\nEuro",
            "Honorarzone V\nsehr hohe\nvon bis\nEuro",
        ],
        [
            "25 000\n50 000",
            "3 120 3 657\n5 804 6 801",
            "3 657 4 339\n6 801 8 071",
            "4 339 5 412\n8 071 10 066",
            "5 412 6 094\n10 066 11 336",
            "6 094 6 631\n11 336 12 333",
        ],
    ]

    def test_every_zone_is_named_with_its_own_range(self):
        rows = self_describing_honorar_rows([self.FIVE_ZONE])
        first = next(r for r in rows if r.startswith("Anrechenbare Kosten 25 000"))
        assert "Honorarzone I 3 120 bis 3 657 Euro" in first
        assert "Honorarzone III 4 339 bis 5 412 Euro" in first
        assert "Honorarzone V 6 094 bis 6 631 Euro" in first

    def test_the_second_stacked_row_is_emitted_too(self):
        rows = self_describing_honorar_rows([self.FIVE_ZONE])
        assert any(r.startswith("Anrechenbare Kosten 50 000") and "Honorarzone I 5 804 bis 6 801" in r for r in rows)

    def test_the_emitted_row_passes_the_guard_that_refuses_the_raw_grid(self):
        # Closing the loop: the raw "500 000 45 232 53 006 …" is refused; this must not be, or
        # the fix would produce rows the guard then blocks.
        for row in self_describing_honorar_rows([self.FIVE_ZONE]):
            assert looks_like_headerless_table_row(row) is False, row

    def test_the_header_newline_is_collapsed_in_the_label(self):
        rows = self_describing_honorar_rows([self.FIVE_ZONE])
        assert all("\n" not in r for r in rows)
        assert all("Anrechenbare Kosten" in r for r in rows)

    def test_a_flaeche_table_uses_its_own_noun_and_unit(self):
        table = [
            ["Fläche\nin Hektar", "Honorarzone I\nvon bis\nEuro", "Honorarzone II\nvon bis\nEuro", "Honorarzone III\nvon bis\nEuro"],
            ["1 000", "70 439 85 269", "85 269 100 098", "100 098 114 927"],
        ]
        rows = self_describing_honorar_rows([table])
        assert rows and rows[0].startswith("Fläche 1 000 Hektar —")
        assert "Honorarzone II 85 269 bis 100 098 Euro" in rows[0]

    def test_a_row_whose_columns_do_not_line_up_is_skipped(self):
        # Three keys but a zone column with only two fee lines: the block cannot be aligned
        # without guessing which key lost its fee. Skipped, not misattributed.
        table = [
            ["Anrechenbare\nKosten", "Honorarzone I\nvon bis", "Honorarzone II\nvon bis"],
            ["25 000\n50 000\n75 000", "3 120 3 657\n5 804 6 801", "3 657 4 339\n6 801 8 071\n9 776 11 601"],
        ]
        assert self_describing_honorar_rows([table]) == []

    def test_a_non_honorar_table_yields_nothing(self):
        table = [["Name", "Age"], ["Alice\nBob", "30\n40"]]
        assert self_describing_honorar_rows([table]) == []

    def test_a_table_with_one_zone_is_not_enough(self):
        # Fewer than two zone columns is not a fee schedule; leave it alone.
        table = [["Anrechenbare\nKosten", "Honorarzone I\nvon bis"], ["25 000", "3 120 3 657"]]
        assert self_describing_honorar_rows([table]) == []
