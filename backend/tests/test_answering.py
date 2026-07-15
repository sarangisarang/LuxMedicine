"""Extraction assembly tests (#18).

No model. `assemble()` is a pure function of (hits, selections) — the part where the
model's output meets the retrieved facts, and the part where fabrication is structurally
prevented rather than checked for.
"""

import uuid

import pytest

from app.schemas.answer import NoAnswerReason
from app.services.answering import (
    MAX_PASSAGES,
    ExtractionResult,
    SelectedQuote,
    assemble,
    render_passages,
)
from app.services.extractor_claude import SYSTEM_PROMPT, render_prompt
from app.services.retrieval import SearchHit

ESC_VERSION = uuid.uuid4()
AHA_VERSION = uuid.uuid4()


def hit(
    *,
    content: str,
    org: str = "ESC",
    version_id: uuid.UUID = ESC_VERSION,
    version_label: str = "2021",
    page: int = 45,
    distance: float = 0.1,
    is_superseded: bool = False,
    superseding: str | None = None,
) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(),
        document_version_id=version_id,
        document_id=uuid.uuid4(),
        document_title=f"{org} Guideline",
        issuing_org=org,
        version_label=version_label,
        section="2.1 Pharmacological therapy",
        page_start=page,
        page_end=page,
        content=content,
        distance=distance,
        is_superseded=is_superseded,
        superseding_version_label=superseding,
    )


DOSE = "The target dose of enalapril is 20 mg twice daily."
MONITORING = "Review renal function after two weeks."


def result(*pairs: tuple[int, str]) -> ExtractionResult:
    return ExtractionResult(quotes=[SelectedQuote(source=s, quote=q) for s, q in pairs])


def citations(answer):
    return [c for g in answer.payload.groups for c in g.citations]


# --- the design this module exists for ---------------------------------------------


def test_provenance_comes_from_the_hit_not_the_model():
    """The load-bearing property.

    The model returns a passage number and a span — nothing else. Page, organisation,
    version and title are looked up from what retrieval actually returned, so a quote
    can never arrive with a page number the model made up. There is no code path where
    the model's opinion about a page reaches a citation.
    """
    hits = [hit(content=DOSE, page=45, org="ESC", version_label="2021")]

    answer = assemble("dose?", hits, result((1, DOSE)), prompt="p", model="m")

    [citation] = citations(answer)
    assert citation.page_start == 45 and citation.page_end == 45
    assert citation.issuing_org == "ESC"
    assert citation.version_label == "2021"
    assert citation.chunk_id == hits[0].chunk_id
    assert citation.quote == DOSE


def test_the_model_never_sees_provenance_to_restate():
    """It cannot be wrong about what it was never shown — and cannot be swayed by whose
    guideline it is reading."""
    hits = [hit(content=DOSE, org="ESC", version_label="2021", page=45)]

    rendered = render_passages(hits)

    assert rendered == [DOSE]
    for leak in ("ESC", "2021", "45", "Guideline"):
        assert leak not in "".join(rendered)


def test_the_model_addresses_passages_by_number_not_uuid():
    """A mistyped UUID resolves to a real chunk somewhere (silently wrong provenance) or
    to nothing (a rejection that looks like fabrication). A bad integer is neither."""
    prompt = render_prompt("dose?", [DOSE, MONITORING])

    assert "[1]" in prompt and "[2]" in prompt
    assert str(ESC_VERSION) not in prompt


def test_the_output_schema_has_no_field_for_a_claim():
    """The MDR positioning at the model's own output surface. If a text field ever
    appears here, the model will fill it — and in a clinical context it fills it with
    advice."""
    assert set(SelectedQuote.model_fields) == {"source", "quote"}
    assert set(ExtractionResult.model_fields) == {"quotes"}


# --- assembly ----------------------------------------------------------------------


def test_quotes_are_grouped_by_source():
    hits = [hit(content=DOSE, org="ESC", version_id=ESC_VERSION), hit(content=MONITORING, org="AHA", version_id=AHA_VERSION, distance=0.2)]

    answer = assemble("dose?", hits, result((1, DOSE), (2, MONITORING)), prompt="p", model="m")

    assert [g.issuing_org for g in answer.payload.groups] == ["ESC", "AHA"]
    assert len(answer.payload.groups[0].citations) == 1


def test_two_quotes_from_one_passage_stay_in_one_group():
    combined = f"{DOSE} {MONITORING}"
    hits = [hit(content=combined)]

    answer = assemble("dose?", hits, result((1, DOSE), (1, MONITORING)), prompt="p", model="m")

    assert len(answer.payload.groups) == 1
    assert len(answer.payload.groups[0].citations) == 2


def test_groups_follow_retrieval_order():
    hits = [
        hit(content=MONITORING, org="AHA", version_id=AHA_VERSION, distance=0.05),
        hit(content=DOSE, org="ESC", version_id=ESC_VERSION, distance=0.5),
    ]

    answer = assemble("dose?", hits, result((2, DOSE), (1, MONITORING)), prompt="p", model="m")

    assert [g.issuing_org for g in answer.payload.groups] == ["AHA", "ESC"]


def test_staleness_travels_onto_the_group(hits=None):
    hits = [hit(content=DOSE, is_superseded=True, superseding="2023")]

    answer = assemble("dose?", hits, result((1, DOSE)), prompt="p", model="m")

    assert answer.payload.groups[0].is_superseded
    assert answer.payload.groups[0].superseding_version_label == "2023"


def test_a_passage_the_model_did_not_quote_forms_no_group():
    """A group with no citations would be a source that appears to have said something."""
    hits = [hit(content=DOSE), hit(content=MONITORING, org="AHA", version_id=AHA_VERSION, distance=0.2)]

    answer = assemble("dose?", hits, result((1, DOSE)), prompt="p", model="m")

    assert [g.issuing_org for g in answer.payload.groups] == ["ESC"]


# --- the model losing track of the numbering ---------------------------------------


@pytest.mark.parametrize("bad", [0, 3, -1, 99])
def test_an_out_of_range_source_is_dropped(bad):
    """A mis-numbered selection has no correct interpretation — guessing which passage
    was meant would invent provenance, which is the thing this design exists to prevent."""
    hits = [hit(content=DOSE), hit(content=MONITORING, distance=0.2)]

    answer = assemble("dose?", hits, result((bad, DOSE)), prompt="p", model="m")

    assert answer.invalid_sources == 1
    assert answer.payload.no_answer_reason is NoAnswerReason.SOURCES_DO_NOT_ANSWER


def test_a_good_selection_survives_a_bad_neighbour():
    hits = [hit(content=DOSE), hit(content=MONITORING, distance=0.2)]

    answer = assemble("dose?", hits, result((1, DOSE), (99, "invented")), prompt="p", model="m")

    assert answer.invalid_sources == 1
    assert [c.quote for c in citations(answer)] == [DOSE]


def test_only_the_shown_passages_are_addressable():
    """The model is shown MAX_PASSAGES; a number beyond that names a passage it never
    saw, whatever retrieval returned."""
    hits = [hit(content=f"passage {i}", distance=i / 100) for i in range(MAX_PASSAGES + 5)]

    assert len(render_passages(hits)) == MAX_PASSAGES

    answer = assemble("q", hits, result((MAX_PASSAGES + 1, "passage 12")), prompt="p", model="m")
    assert answer.invalid_sources == 1


# --- the no-answer paths (#20) -----------------------------------------------------


def test_nothing_retrieved_says_the_corpus_has_nothing():
    answer = assemble("q", [], result(), prompt="p", model="m")

    assert answer.payload.no_answer_reason is NoAnswerReason.NO_RELEVANT_SOURCES


def test_passages_read_but_none_answer_says_so():
    """Different from the above, and a clinician needs the difference: one is "the
    guidelines are silent", the other is "the guidelines were read and do not cover
    this"."""
    answer = assemble("q", [hit(content=DOSE)], result(), prompt="p", model="m")

    assert answer.payload.no_answer_reason is NoAnswerReason.SOURCES_DO_NOT_ANSWER


def test_a_refusal_is_an_empty_answer_not_a_crash():
    """A clinical question can trip a safety classifier. `None` from the extractor must
    land as "these passages do not answer" — which is true — rather than a 500."""
    answer = assemble("q", [hit(content=DOSE)], None, prompt="p", model="m")

    assert answer.payload.no_answer_reason is NoAnswerReason.SOURCES_DO_NOT_ANSWER
    assert answer.payload.groups == []


# --- the prompt --------------------------------------------------------------------


def test_the_prompt_forbids_the_things_that_defeat_validation():
    for rule in ("character-for-character", "paraphrase", "ellipsis", "contiguous"):
        assert rule in SYSTEM_PROMPT


def test_the_prompt_makes_the_empty_answer_easy_to_take():
    """#20's requirement, in the prompt: an empty answer must be as reachable as any
    other, or the model invents one to be helpful."""
    assert "empty answer is correct" in SYSTEM_PROMPT


def test_the_prompt_says_what_happens_to_an_inexact_quote():
    """The model should know a near-miss is discarded rather than helpfully approximated
    — #19 is a fact about the system, not a secret."""
    assert "discarded" in SYSTEM_PROMPT
