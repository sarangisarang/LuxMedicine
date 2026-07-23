"""The rule that retires a superseded self-describing row.

This decides which rows stop answering, so its failure modes are asymmetric and both are
tested. Missing a pair leaves a truncated row competing with its completed twin — how
"ii. Systolic ≥160 mm Hg or" kept out-ranking the full threshold. Inventing a pair silently
retires a row that was never replaced, which is content loss no reader would notice.

The measurement that first found these rows had a bug on this exact path (it compared raw
labels and missed that a completed row also loses its enumerator, reporting 60 of 88). That is
why the normalisation is pinned here rather than trusted.
"""

from __future__ import annotations

from app.services.row_supersession import looks_truncated, replaces

CELLS = "Cu-IUD: 1, LNG-IUD: 2, Implant: 2, DMPA: 3, POP: 2, CHC: 4"
OTHER_CELLS = "Cu-IUD: 1, LNG-IUD: 1, Implant: 1, DMPA: 1, POP: 1, CHC: 1"


class TestReplaces:
    def test_a_completed_label_replaces_its_truncated_self(self):
        assert replaces(
            f"b. Decompensated (impaired — {CELLS}",
            f"b. Decompensated (impaired liver function) — {CELLS}",
        )

    def test_the_enumerator_and_a_new_parent_do_not_break_the_pairing(self):
        """The case the first measurement missed: completion both adds a parent segment and
        strips the bullet, so the raw labels share no prefix at all."""
        assert replaces(
            f"ii. Systolic ≥160 mm Hg or — {CELLS}",
            f"Elevated blood pressure levels — Systolic ≥160 mm Hg or diastolic ≥100 mm Hg — {CELLS}",
        )

    def test_a_different_category_vector_is_a_different_row(self):
        """The strongest guard against inventing a pair: two conditions can print almost the
        same label and mean opposite things, and the categories are what tell them apart."""
        assert not replaces(
            f"b. Decompensated (impaired — {CELLS}",
            f"b. Decompensated (impaired liver function) — {OTHER_CELLS}",
        )

    def test_a_complete_label_is_never_superseded(self):
        """A finished row is not a draft. Without this, any longer label on the page could
        retire a perfectly good row."""
        assert not replaces(
            f"a. Varicose veins — {CELLS}",
            f"a. Varicose veins and telangiectasia — {CELLS}",
        )

    def test_an_identical_row_is_not_a_replacement(self):
        assert not replaces(f"b. Decompensated (impaired — {CELLS}", f"b. Decompensated (impaired — {CELLS}")

    def test_a_shorter_label_never_replaces_a_longer_one(self):
        assert not replaces(
            f"b. Decompensated (impaired liver function) — {CELLS}",
            f"b. Decompensated (impaired — {CELLS}",
        )

    def test_an_unrelated_condition_is_not_a_replacement(self):
        assert not replaces(f"a. Thrombophilia (e.g., — {CELLS}", f"a. Cirrhosis (compensated) — {CELLS}")

    def test_non_self_describing_text_is_never_paired(self):
        assert not replaces("ordinary prose about contraception", "more ordinary prose")
        assert not replaces(f"a. Something (cut — {CELLS}", "ordinary prose")

    def test_a_fee_row_pairs_on_the_same_rule(self):
        """The honorar family is matched too, so a future fee-label fix retires its own
        predecessors rather than leaving both to compete.

        Hypothetical by construction: the fee rows carry no truncation today (337 measured,
        0 unbalanced, 0 dangling). It is pinned so the rule stays usable if that changes.
        The bracket case is asserted as well as the German conjunction, because a bracket is
        language-neutral and does not depend on the word list being complete.
        """
        cells = "Honorarzone I 45 232 bis 53 006 Euro, Honorarzone II 53 006 bis 62 900 Euro"
        assert replaces(f"Anrechenbare Kosten (netto — {cells}", f"Anrechenbare Kosten (netto, ohne USt) — {cells}")
        assert replaces(f"Anrechenbare Kosten und — {cells}", f"Anrechenbare Kosten und Nebenkosten — {cells}")


class TestLooksTruncated:
    def test_an_unclosed_bracket_is_truncated(self):
        assert looks_truncated("b. Decompensated (impaired")

    def test_a_dangling_conjunction_is_truncated(self):
        assert looks_truncated("ii. Systolic ≥160 mm Hg or")
        assert looks_truncated("b. Menarche to <18 years and")
        assert looks_truncated("Thrombophilia (e.g.,")

    def test_a_finished_label_is_not(self):
        assert not looks_truncated("a. Varicose veins")
        assert not looks_truncated("d. Family history (first-degree relatives)")
        assert not looks_truncated("Migraine — With aura")

    def test_an_empty_label_is_not_truncated(self):
        assert not looks_truncated("")
        assert not looks_truncated("   ")
