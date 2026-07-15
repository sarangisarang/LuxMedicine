"""Verbatim citation validation tests (#19).

Every test here is an attempt to smuggle text past the check. A validator that only sees
honest input proves nothing — the entire job is what it refuses.
"""

import uuid

import pytest

from app.schemas.answer import AnswerPayload, Citation, NoAnswerReason, SourceGroup
from app.services.validation import normalise, validate_answer

CHUNK_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_CHUNK_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
VERSION_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")

SOURCE = (
    "The target dose of enalapril is 20 mg twice daily in patients with heart failure "
    "and reduced ejection fraction, unless eGFR is below 30 mL/min.\n"
    "Monitoring of renal function is recommended after two weeks."
)

CHUNKS = {CHUNK_ID: SOURCE, OTHER_CHUNK_ID: "Colorectal cancer screening begins at age 45."}


def citation(quote: str, *, chunk_id: uuid.UUID = CHUNK_ID) -> Citation:
    return Citation(
        chunk_id=chunk_id,
        document_version_id=VERSION_ID,
        document_title="ESC Heart Failure Guidelines",
        issuing_org="ESC",
        version_label="2021",
        page_start=45,
        page_end=45,
        quote=quote,
    )


def payload(*citations: Citation) -> AnswerPayload:
    return AnswerPayload(
        groups=[
            SourceGroup(
                issuing_org="ESC",
                version_label="2021",
                document_version_id=VERSION_ID,
                citations=list(citations),
            )
        ]
    )


def surviving_quotes(result) -> list[str]:
    return [c.quote for g in result.payload.groups for c in g.citations]


# --- what passes -------------------------------------------------------------------


def test_a_verbatim_quote_survives():
    result = validate_answer(payload(citation("The target dose of enalapril is 20 mg twice daily")), CHUNKS)

    assert result.is_clean
    assert len(surviving_quotes(result)) == 1


def test_a_quote_spanning_a_line_break_survives():
    """The model returns the newline as a space. Rejecting that would reject faithful
    quotes — and teach whoever tunes the prompt to relax the check instead."""
    quote = "below 30 mL/min. Monitoring of renal function is recommended"
    result = validate_answer(payload(citation(quote)), CHUNKS)

    assert result.is_clean


def test_unicode_forms_do_not_matter():
    """Extraction normalises to NFC; a model may emit NFD. Same characters."""
    assert normalise("é") == normalise("é")


# --- what does not -----------------------------------------------------------------


def test_an_invented_quote_is_rejected():
    """The mechanism, stated plainly: text that is not in the source cannot pass, because
    the check is a substring test rather than a judgement."""
    result = validate_answer(
        payload(citation("Enalapril is contraindicated in renal impairment")), CHUNKS
    )

    assert not result.is_clean
    assert surviving_quotes(result) == []
    assert result.payload.rejected_citations == 1
    assert "does not appear" in result.rejected[0].reason


def test_a_quote_with_an_altered_number_is_rejected():
    """The highest-stakes edit available: one digit, and the citation still looks real."""
    result = validate_answer(citation_payload := payload(citation("target dose of enalapril is 40 mg")), CHUNKS)

    assert not result.is_clean
    assert citation_payload.groups[0].citations[0].quote.endswith("40 mg")


def test_an_elided_quote_is_rejected():
    """A quote is contiguous. "the dose is ... contraindicated" would join two distant
    spans into a claim the source never makes — so two spans are two citations."""
    result = validate_answer(
        payload(citation("The target dose of enalapril is ... Monitoring of renal function")), CHUNKS
    )

    assert not result.is_clean


def test_a_negation_smuggled_into_a_real_sentence_is_rejected():
    """The dangerous edit: almost the source, inverted."""
    result = validate_answer(
        payload(citation("Monitoring of renal function is not recommended after two weeks")), CHUNKS
    )

    assert not result.is_clean


def test_a_quote_from_another_retrieved_chunk_is_rejected():
    """Real text, wrong provenance. The page number would send a clinician to a page that
    does not contain it."""
    result = validate_answer(
        payload(citation("Colorectal cancer screening begins at age 45", chunk_id=CHUNK_ID)), CHUNKS
    )

    assert not result.is_clean


def test_citing_a_chunk_that_was_never_retrieved_is_rejected():
    """An invented provenance is worse than an invented quote: the id resolves to real
    text somewhere, so nothing downstream looks wrong."""
    result = validate_answer(
        payload(citation("anything at all", chunk_id=uuid.uuid4())), CHUNKS
    )

    assert not result.is_clean
    assert "not retrieved" in result.rejected[0].reason


def test_case_is_not_normalised():
    """"MAY" and "may" are different words in guideline language."""
    result = validate_answer(payload(citation("THE TARGET DOSE OF ENALAPRIL IS 20 MG")), CHUNKS)
    assert not result.is_clean


def test_punctuation_is_not_normalised():
    """A comma changes a dose list."""
    result = validate_answer(payload(citation("The target dose of enalapril is 20 mg, twice daily")), CHUNKS)
    assert not result.is_clean


# --- the failure mode --------------------------------------------------------------


def test_a_good_quote_survives_a_bad_neighbour():
    """Per-citation, not all-or-nothing. Each surviving quote is independently proven to
    be in its source, so discarding real guidance over a neighbour's fault would cost
    more than it protects."""
    result = validate_answer(
        payload(
            citation("The target dose of enalapril is 20 mg twice daily"),
            citation("Enalapril is contraindicated in renal impairment"),
        ),
        CHUNKS,
    )

    assert len(surviving_quotes(result)) == 1
    assert result.payload.rejected_citations == 1


def test_a_rejection_is_counted_not_hidden():
    """A clinician seeing four quotes cannot tell that a fifth was dropped, and a model
    fabricating text right now is not an event to swallow."""
    result = validate_answer(payload(citation("invented"), citation("also invented")), CHUNKS)

    assert result.payload.rejected_citations == 2
    assert len(result.rejected) == 2


def test_a_group_with_nothing_left_is_dropped():
    """An empty group is a heading with nothing under it — a source that appears to have
    said something."""
    result = validate_answer(payload(citation("invented")), CHUNKS)

    assert result.payload.groups == []


def test_a_clean_answer_reports_zero_rejections():
    result = validate_answer(payload(citation("Monitoring of renal function is recommended")), CHUNKS)

    assert result.payload.rejected_citations == 0
    assert result.is_clean


def test_an_empty_answer_validates():
    """#20's path. No groups, nothing to check, and no reason to fail."""
    result = validate_answer(
        AnswerPayload(no_answer_reason=NoAnswerReason.NO_RELEVANT_SOURCES), {}
    )

    assert result.is_clean
    assert result.payload.no_answer_reason is NoAnswerReason.NO_RELEVANT_SOURCES


# --- the schema itself -------------------------------------------------------------


def test_a_group_cannot_be_built_without_citations():
    """#21, structurally. The hole that carried ExtractedStatement.text is gone: there is
    no field left for a model to write a claim into, so "a statement without a citation"
    is unrepresentable rather than merely forbidden."""
    with pytest.raises(ValueError):
        SourceGroup(
            issuing_org="ESC", version_label="2021", document_version_id=VERSION_ID, citations=[]
        )


def test_a_citation_cannot_carry_an_empty_quote():
    with pytest.raises(ValueError):
        citation("")


def test_the_schema_has_no_field_for_a_claim():
    """The MDR positioning, as a test. If a field ever appears here that holds free
    clinical text, this fails — and that is the moment to re-open the question rather
    than widen the schema."""
    assert set(Citation.model_fields) == {
        "chunk_id",
        "document_version_id",
        "document_title",
        "issuing_org",
        "version_label",
        "page_start",
        "page_end",
        "section",
        "quote",
    }
    assert set(SourceGroup.model_fields) == {
        "issuing_org",
        "version_label",
        "document_version_id",
        "citations",
        "is_superseded",
        "superseding_version_label",
    }


# --- #20: an empty answer must explain itself --------------------------------------


def test_an_answer_cannot_be_blank_without_a_reason():
    """The invariant. Otherwise the worst outcome is the easiest to reach: a clinician
    sees a blank result and cannot tell "the guidelines are silent" from "we broke"."""
    with pytest.raises(ValueError, match="no_answer_reason"):
        AnswerPayload(groups=[])


def test_the_reason_is_a_closed_set_not_prose():
    """Free text here would be the ExtractedStatement.text hole again — a model asked to
    explain an empty answer writes "no guidance found, though enalapril is generally
    used", which is advice arriving through the field meant to say there is none."""
    with pytest.raises(ValueError):
        AnswerPayload(no_answer_reason="no guidance found, though enalapril is generally used")


def test_rejecting_everything_reports_our_failure_not_the_corpus_s():
    """The distinction that must never blur. "The corpus has nothing" is a fact about the
    guidelines; "the quotes did not verify" is a fact about us malfunctioning. Reporting
    the second as the first would tell a clinician the guidelines are silent on their
    question — the quietest lie this system could tell."""
    result = validate_answer(payload(citation("entirely invented")), CHUNKS)

    assert result.payload.groups == []
    assert result.payload.no_answer_reason is NoAnswerReason.VERIFICATION_FAILED
    assert result.payload.no_answer_reason is not NoAnswerReason.NO_RELEVANT_SOURCES


def test_validation_cannot_produce_an_unexplained_blank():
    """model_copy skips validation, so rebuilding the payload is what keeps the invariant
    reachable. If this ever regresses, the schema guard is bypassed rather than broken —
    which is worse, because everything still looks green."""
    result = validate_answer(payload(citation("invented")), CHUNKS)

    # Round-trips through validation: proves the object it returns is actually valid.
    AnswerPayload.model_validate(result.payload.model_dump())


def test_conflicts_do_not_survive_an_emptied_answer():
    """A conflict references groups. Keeping one when every group was dropped would
    point at sources the answer no longer contains."""
    result = validate_answer(payload(citation("invented")), CHUNKS)
    assert result.payload.conflicts == []


def test_a_surviving_answer_needs_no_reason():
    result = validate_answer(payload(citation("Monitoring of renal function is recommended")), CHUNKS)

    assert result.payload.groups
    assert result.payload.no_answer_reason is None, "there is an answer; nothing to explain"
