"""The #48 safety net (app/services/table_guard.py).

Bar set before the guard was built, from quotes taken off live runs on 2026-07-17: it must
refuse every known headerless category-table row, and keep every real prose answer the system
gives. The GOOD list is what a clinician correctly receives today; a regression here withholds
real guidance, which is the failure #48's fix must never introduce while making the system safe.
"""

from __future__ import annotations

import uuid

import pytest

from app.schemas.answer import AnswerPayload, Citation, NoAnswerReason, SourceGroup
from app.services.table_guard import guard_table_rows, looks_like_headerless_table_row

# Real prose answers from live runs — MUST be kept. Each spells its category inline or carries
# no bare category cell at all.
GOOD = [
    "A person aged ≥35 years who smokes ≥15 cigarettes per day should not use COCs "
    "because of unacceptable",
    "Patients with obesity (BMI ≥30 kg/m2) can use implants (U.S. MEC 1)",
    "Patients with obesity (BMI ≥30 kg/m2) can use IUDs (U.S. MEC 1)",
    "patients with benign liver tumors, viral hepatitis, or cirrhosis can use (U.S. MEC 1) "
    "or generally",
    "All POPs may be started at any time, including immediately postpartum (U.S. MEC 2 if <30",
    "Patients with hypertension, diabetes, iron-deficiency anemia, thrombophilia, cervical "
    "intraepithelial neoplasia, cervical cancer, STIs, or HIV infection can use (U.S. MEC 1) "
    "or generally can use (U.S. MEC 2)",
    "consideration should be given to transitioning from CHCs to a progestin-only or "
    "nonhormonal method",
    "Limited evidence demonstrated that women using LNG-IUDs do not have an increased risk "
    "for ischemic stroke compared with women not using hormonal contraceptives",
    "although cardioselective beta-blockers may be tolerated",
]

# Headerless category-table rows — MUST be refused. The bare trailing digit is a category
# whose column heading is not in the span.
BAD = [
    "ii. With aura 1 1 1 —",
    "b. Migraine\ni. Without aura (includes menstrual migraine) 1\nii. With aura 1",
    "ii. With aura 1 1 1 1 1 4*",
    "Cervical intraepithelial 1 2",
    "a. Positive (or unknown) 1* 1* 2* 2* 3* 3* 2* 4*",
]


@pytest.mark.parametrize("quote", GOOD)
def test_real_answers_are_kept(quote):
    assert looks_like_headerless_table_row(quote) is False, quote


@pytest.mark.parametrize("quote", BAD)
def test_headerless_rows_are_refused(quote):
    assert looks_like_headerless_table_row(quote) is True, quote


class TestSelfLabelling:
    """A number that names its own scale needs no column heading — the measured false-positive
    class (Step/Grade/Class...) that a naive 'trailing digit' rule would wrongly refuse."""

    @pytest.mark.parametrize(
        "quote",
        [
            "The recommended approach is Step 2",
            "classified as Grade 3",
            "asthma severity is Step 1 Step 2 Step 3",
            "see Figure 2",
            "continue for 1 year or up to age 2",  # 'age 2' is self-labelling
        ],
    )
    def test_labelled_numbers_are_not_headerless_rows(self, quote):
        assert looks_like_headerless_table_row(quote) is False, quote


class TestEdges:
    def test_a_bare_digit_fragment_with_no_label_is_not_a_row(self):
        # A stray "1" or "1 1" without a text label is a fragment, not a labelled table row —
        # #19 handles fabricated fragments; this guard is about mislabelled real rows.
        assert looks_like_headerless_table_row("1 1 1") is False
        assert looks_like_headerless_table_row("1") is False

    def test_a_short_label_does_not_trip_it(self):
        # "a. 1" — an outline marker, not a condition. Below the label-length floor.
        assert looks_like_headerless_table_row("a. 1") is False

    def test_prose_ending_in_a_number_with_units_is_kept(self):
        assert looks_like_headerless_table_row("the target dose is 20 mg daily") is False

    def test_multiline_flags_if_any_line_is_a_row(self):
        assert looks_like_headerless_table_row("Some prose here.\nWith aura 4") is True


def _payload(*quotes: str, reason=None) -> AnswerPayload:
    version_id = uuid.uuid4()
    return AnswerPayload(
        query_language="English",
        groups=[
            SourceGroup(
                issuing_org="CDC",
                version_label="2024",
                document_version_id=version_id,
                citations=[
                    Citation(
                        chunk_id=uuid.uuid4(),
                        document_version_id=version_id,
                        document_title="CDC U.S. MEC",
                        issuing_org="CDC",
                        version_label="2024",
                        quote=q,
                        page_start=1,
                        page_end=1,
                    )
                    for q in quotes
                ],
            )
        ]
        if quotes
        else [],
        no_answer_reason=reason,
        rejected_citations=0,
    )


class TestGuardTableRows:
    def test_a_clean_answer_passes_through_untouched(self):
        p = _payload("Patients with obesity (BMI ≥30 kg/m2) can use IUDs (U.S. MEC 1)")
        result = guard_table_rows(p)
        assert result.dropped == []
        assert result.payload is p  # not rebuilt when nothing changes

    def test_an_emptied_answer_declines_as_table_not_citable(self):
        """The #48 case: the only citation is a headerless row, so the answer empties — and the
        reason must say the table is uncitable, NOT that the sources are silent."""
        p = _payload("ii. With aura 1 1 1 —")
        result = guard_table_rows(p)
        assert len(result.dropped) == 1
        assert result.payload.groups == []
        assert result.payload.no_answer_reason == NoAnswerReason.TABLE_NOT_CITABLE

    def test_a_prose_quote_survives_a_table_row_neighbour(self):
        """Per citation, not all-or-nothing — a real answer is never discarded over a table
        row beside it."""
        p = _payload(
            "Patients with obesity (BMI ≥30 kg/m2) can use IUDs (U.S. MEC 1)",
            "ii. With aura 1 1 1 —",
        )
        result = guard_table_rows(p)
        assert len(result.dropped) == 1
        assert len(result.payload.groups) == 1
        assert len(result.payload.groups[0].citations) == 1
        assert result.payload.no_answer_reason is None  # still answered

    def test_it_does_not_overwrite_an_existing_reason(self):
        p = _payload("ii. With aura 1", reason=NoAnswerReason.VERIFICATION_FAILED)
        result = guard_table_rows(p)
        assert result.payload.no_answer_reason == NoAnswerReason.VERIFICATION_FAILED
