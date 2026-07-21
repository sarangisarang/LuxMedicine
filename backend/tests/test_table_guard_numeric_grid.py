"""The numeric-grid half of the table guard, against real HOAI Honorartafel rows.

Why this file exists. #48 was found in the clinical corpus as a US MEC category cell quoted
without its column heading, and the guard written for it looks for a *text* row label
followed by cells in the range 1-4. HOAI's fee tables are the same failure wearing different
clothes: the row label is a number (the anrechenbare Kosten), the cells are five-digit euro
amounts, and the Honorarzone headings that say which column is which are in a header row no
chunk carries. Read against the PDF, a quote of one such row tells an architect a fee
without telling them which Honorarzone it is the fee for.

The rows below are copied verbatim out of the extractor's own output on
storage/legal_clean/hoai_official_gii.pdf — not invented to match the rule. A fixture
written to fit the regex would prove the regex matches itself.

The negative cases are the ones that decide whether this rule is safe to run over the
clinical corpus too, since the guard is not sector-aware: prose containing numbers, short
numeric lists, and the self-describing "CHC: 4" form that services/table_extraction.py emits
must all survive.
"""

from app.services.table_guard import looks_like_headerless_table_row

# Verbatim from the extractor on pages 28-29, 33-35 and 48 of the official HOAI.
HOAI_FEE_ROWS = [
    "100 000 10 079 12 005 12 005 13 932 13 932 15 859 15 859 17 637 17 637 19 564",
    "500 000 34 865 41 530 41 530 48 195 48 195 54 861 54 861 61 013 61 013 67 679",
    "1 000 000 59 264 70 594 70 594 81 924 81 924 93 254 93 254 103 712 103 712 115 042",
    "150 000 2 061 2 676 2 676 3 291 3 291 3 942 3 942 4 557 4 557 5 171",
    "20 000 000 592 324 7",
]


def test_a_real_honorartafel_row_is_refused():
    for row in HOAI_FEE_ROWS:
        assert looks_like_headerless_table_row(row), (
            f"this fee row would be quoted to an architect with no Honorarzone heading: {row!r}"
        )


def test_a_fee_row_inside_a_longer_quote_is_still_refused():
    """The guard reads per line, so a grid row does not become safe by being surrounded."""
    quote = (
        "Die Honorartafel für Grundleistungen bei Gebäuden und Innenräumen lautet:\n"
        "500 000 34 865 41 530 41 530 48 195 48 195 54 861 54 861 61 013 61 013 67 679\n"
        "Die Werte sind Nettobeträge."
    )
    assert looks_like_headerless_table_row(quote)


# --- what must survive --------------------------------------------------------------


def test_german_statutory_prose_with_numbers_survives():
    """The paragraphs that make this corpus worth having. If the rule eats these, the legal
    sector answers nothing and the guard has traded one failure for a worse one."""
    prose = [
        "Das Honorar richtet sich nach der Honorarzone, der die Leistung angehört.",
        "Die Honorare für Grundleistungen bei Gebäuden sind in § 35 Absatz 1 geregelt.",
        "Bei anrechenbaren Kosten über 25 000 Euro und bis 25 000 000 Euro gilt Absatz 2.",
        "Die Leistungsphasen 1 bis 9 sind in Anlage 10 Nummer 10.1 beschrieben.",
    ]
    for line in prose:
        assert not looks_like_headerless_table_row(line), f"refused real statutory prose: {line!r}"


def test_a_short_numeric_list_survives():
    """Under the threshold on purpose — "1 2 3 4" is an enumeration, not a grid."""
    assert not looks_like_headerless_table_row("1 2 3 4 5")


def test_the_self_describing_row_the_table_extractor_emits_still_survives():
    """services/table_extraction.py exists to make table rows quotable by naming their own
    column. Its output must pass both halves of this guard, or the fix for #48 would be
    refused by the net that stands in for it."""
    assert not looks_like_headerless_table_row("CHC: 4")
    assert not looks_like_headerless_table_row("Honorarzone III von: 48 195 bis: 54 861")


def test_the_us_mec_case_the_guard_was_written_for_is_untouched():
    """The original #48 row. The new rule is additive; this must not depend on it."""
    assert looks_like_headerless_table_row("ii. With aura 1")
