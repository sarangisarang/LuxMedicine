"""Why a quote failed #19 (#43).

The case that produced this file is the last test in it, and it is not synthetic. The
model, asked when to refer a CKD patient, returned:

    "Rapid sustained decline in GFR could also be considered an indication for referral"

#19 rejected it. My first classifier called it a paraphrase — 60% word overlap — and that
label would have sent the fix to the prompt, which already says "never paraphrase" and
which the model had obeyed. It had not reworded anything. It had *assembled*: "indication
for referral" is in the corpus attached to proteinuria (p82, p83), "rapid sustained
decline in GFR" is a real concept from elsewhere, and the sentence is neither.

The claim is also, almost certainly, clinically correct. That is what makes it the worst
case: a system returning true, unsourced clinical statements is an adviser, not a search
engine, and #19 caught this because it was unsourced — not because it was wrong.
"""

import pytest

from app.services.quote_diagnosis import Verdict, diagnose

PASSAGE = (
    "Progressive kidney disease requires the need for more aggressive assessment and "
    "treatment, which may include referral to a specialist."
)


def corpus(*phrases: str):
    """Stand-in for 'does any chunk contain this phrase'."""
    haystack = " ".join(phrases).lower()
    return lambda gram: gram.lower() in haystack


# --- the ordinary cases -------------------------------------------------------------


def test_a_verbatim_quote_is_clean():
    assert diagnose("requires the need for more aggressive", PASSAGE).verdict is Verdict.CLEAN


def test_whitespace_is_typography_not_a_failure():
    """#19 normalises NFC and whitespace runs, so these never reach the clinician as
    rejections. Classifying them as anything else would inflate every rate we measure."""
    d = diagnose("requires  the need   for more aggressive", PASSAGE)

    assert d.verdict is Verdict.TYPOGRAPHY
    assert not d.is_invention


def test_edges_are_a_boundary_problem():
    d = diagnose('"requires the need for more aggressive."', PASSAGE)

    assert d.verdict is Verdict.BOUNDARY
    assert not d.is_invention


def test_the_right_words_in_the_wrong_passage_is_not_an_invention():
    """The model quoted honestly and numbered it wrong. Nothing was made up, and calling
    it a hallucination would send someone to fix the model instead of the numbering."""
    other = "Referral is recommended when the eGFR falls below 30 ml/min/1.73m2."
    d = diagnose("falls below 30 ml/min", PASSAGE, other_passages=[other])

    assert d.verdict is Verdict.MIS_CITED
    assert not d.is_invention


# --- the two that matter ------------------------------------------------------------


def test_two_real_fragments_stitched_is_synthesis():
    """Both halves exist. The sentence does not. This is the shape of the real rejection
    that opened #43, and the one a word-overlap score reads as a paraphrase."""
    d = diagnose(
        "rapid sustained decline in GFR is an indication for referral",
        PASSAGE,
        corpus_contains=corpus(
            "a change in quantity of proteinuria is an indication for referral",
            "rapid sustained decline in GFR over three months",
        ),
    )

    assert d.verdict is Verdict.SYNTHESIS
    assert d.is_invention


def test_text_found_nowhere_is_a_parametric_leak():
    """The model answered from training, not from the passages. Different problem, and
    the prompt is not where it lives."""
    d = diagnose(
        "patients should commence dialysis when the eGFR reaches 10",
        PASSAGE,
        corpus_contains=corpus("nothing resembling that appears in this corpus"),
    )

    assert d.verdict is Verdict.PARAMETRIC_LEAK
    assert d.is_invention


def test_synthesis_and_leak_are_not_collapsed():
    """They have different answers — synthesis is a prompt and chunking problem, a leak is
    a model problem — so a classifier that cannot tell them apart is a classifier that
    picks the wrong fix half the time."""
    corpus_has = corpus("an indication for referral", "rapid sustained decline in GFR")

    stitched = diagnose(
        "rapid sustained decline in GFR is an indication for referral",
        PASSAGE,
        corpus_contains=corpus_has,
    )
    invented = diagnose(
        "commence dialysis when the eGFR reaches ten millilitres",
        PASSAGE,
        corpus_contains=corpus_has,
    )

    assert stitched.verdict is not invented.verdict


def test_without_the_corpus_neither_can_be_named():
    """`corpus_contains` is not optional in spirit: with only the cited passage to compare
    against, an assembled sentence and a reworded one look identical. The honest answer
    then is the weaker one."""
    d = diagnose("rapid sustained decline in GFR is an indication for referral", PASSAGE)

    assert d.verdict is Verdict.PARAPHRASE, "no corpus, no stronger claim"


# --- the distinction the whole file exists for ---------------------------------------


@pytest.mark.parametrize(
    "verdict,invention",
    [
        (Verdict.TYPOGRAPHY, False),
        (Verdict.BOUNDARY, False),
        (Verdict.MIS_CITED, False),
        (Verdict.PARAPHRASE, False),
        (Verdict.SYNTHESIS, True),
        (Verdict.PARAMETRIC_LEAK, True),
    ],
)
def test_only_two_verdicts_mean_the_model_invented_something(verdict, invention):
    """A rejection rate that lumps these together says nothing. "The model failed 30% of
    the time" is a different sentence depending on whether it was whitespace or fabrication,
    and only one of them is a reason to stop."""
    from app.services.quote_diagnosis import Diagnosis

    assert Diagnosis(verdict, "").is_invention is invention
