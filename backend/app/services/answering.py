"""Turning retrieved passages into an extractive answer (#18).

**The model chooses two things and nothing else: which passage, and which span of it.**

That is the whole design. An earlier sketch had the model emit a full `Citation` —
chunk_id, document title, issuing organisation, version label, page numbers, quote. It
would have worked, and it would have been wrong: every one of those fields is a fact we
already know, and asking a model to restate a known fact is inviting it to restate it
incorrectly. A quote can be perfectly verbatim while the page number beside it is off by
one, and #19 would pass it — because #19 checks the quote, not the page. Provenance is
looked up from the retrieved hit instead. The model cannot get the page wrong because it
never touches the page.

Passages are numbered `[1]`, `[2]`, `[3]` rather than addressed by UUID, for the same
reason: a model asked to copy a 36-character identifier will eventually mistype one, and
a mistyped UUID either resolves to a real chunk somewhere (silently wrong provenance) or
to nothing (a rejection that looks like a fabrication). An out-of-range integer is
neither — it is obviously, checkably wrong.

So the model's entire output surface is `{source: int, quote: str}` per selection. There
is no field for a claim, a recommendation, or a summary. The extractive positioning is
enforced by the schema, here and in app/schemas/answer.py — not by asking the prompt
nicely.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.core.vocabulary import Sector
from app.schemas.answer import AnswerPayload, Citation, NoAnswerReason, SourceGroup
from app.services.grouping import group_hits
from app.services.retrieval import SearchHit

# The model sees this many passages at most. Beyond it, the marginal passage is one the
# retrieval ranked worst and the model is least likely to quote — while still costing
# input tokens on every query.
MAX_PASSAGES = 12


class SelectedQuote(BaseModel):
    """One span the model chose to quote, and where it says it came from."""

    source: int = Field(
        description="The [n] marker of the passage this quote is taken from, exactly as shown."
    )
    quote: str = Field(
        min_length=1,
        description=(
            "A contiguous span copied character-for-character from that passage. Never "
            "paraphrase, never join distant spans with an ellipsis, never correct the "
            "source's wording. Two separate spans are two entries."
        ),
    )


class ExtractionResult(BaseModel):
    """Everything the model is permitted to return.

    Note what is absent: no summary, no recommendation, no free-text reason. A model
    given a text field will fill it, and what it fills it with in a clinical context is
    advice.
    """

    quotes: list[SelectedQuote] = Field(
        default_factory=list,
        description=(
            "Spans from the passages that answer the question. Empty if none of them do — "
            "an empty answer is a correct outcome, and inventing one is not."
        ),
    )


@runtime_checkable
class Extractor(Protocol):
    """Chooses passages and spans. Never writes prose."""

    def extract(
        self, question: str, passages: list[str], sector: Sector = Sector.MEDICAL
    ) -> ExtractionResult | None:
        """Return the selections, or None if the model declined to answer at all.

        `sector` selects the system prompt — the passages are statutes or they are
        clinical guidance, and the wording that tells the model which it is reading
        differs (see extractor_claude). Defaulted so the dozens of three-line fake
        extractors in the suite keep working unchanged, and because the default can only
        be MEDICAL: a legal question reaching the clinical prompt reads statutes as
        guidelines, which is wrong but visible in the output. There is no value of this
        parameter that produces a confidently mislabelled answer.
        """
        ...


@dataclass(frozen=True)
class Answer:
    payload: AnswerPayload
    prompt: str
    model: str

    # Selections discarded before validation because `source` named no passage. Distinct
    # from #19's rejections, which are quotes that failed verbatim matching — this one is
    # the model losing track of the numbering it was handed.
    invalid_sources: int = 0


def render_passages(hits: list[SearchHit], *, limit: int = MAX_PASSAGES) -> list[str]:
    """The passage list the model sees, in retrieval order.

    Content only — no organisation, no page, no version. Those would give the model
    material to restate, and restating provenance is exactly what it must not do. It
    also cannot be influenced by a source's identity if it cannot see it.
    """
    return [hit.content for hit in hits[:limit]]


def _incomplete_sources(hits: list[SearchHit]) -> dict[str, int]:
    """Searched documents known to have pages extraction could not read, by title.

    `unreadable_pages` is None where nobody measured and [] where it was measured clean —
    different claims, and only a non-empty list is reported. A version appearing on several
    hits contributes once, because the count belongs to the document, not to the hit.
    """
    out: dict[str, int] = {}
    seen: set[uuid.UUID] = set()
    for hit in hits:
        if hit.document_version_id in seen:
            continue
        seen.add(hit.document_version_id)
        if hit.unreadable_pages:
            out[hit.document_title] = out.get(hit.document_title, 0) + len(hit.unreadable_pages)
    return out


def assemble(
    question: str,
    hits: list[SearchHit],
    result: ExtractionResult | None,
    *,
    prompt: str,
    model: str,
    query_language: str | None = None,
) -> Answer:
    """Build the answer from the model's selections and the retrieved facts.

    Every field except `quote` comes from the hit. The model's `source` index is the only
    thing joining the two, and an index outside the list is dropped rather than guessed
    at — a mis-numbered selection has no correct interpretation.
    """
    if not hits:
        return Answer(
            payload=AnswerPayload(
                query_language=query_language,
                no_answer_reason=NoAnswerReason.NO_RELEVANT_SOURCES,
            ),
            prompt=prompt,
            model=model,
        )

    shown = hits[:MAX_PASSAGES]

    if result is None or not result.quotes:
        # The model read the passages and none of them answer. That is a fact about the
        # corpus, not about us — distinct from #19 rejecting fabricated quotes.
        #
        # And it is a fact with a qualifier the clinician could not previously see: some of
        # what was searched is missing. `unreadable_pages` travels on every hit precisely so
        # that "the guideline does not say" and "the page where it says it could not be read"
        # do not arrive as the same sentence — but only the answered path was using it.
        return Answer(
            payload=AnswerPayload(
                query_language=query_language,
                no_answer_reason=NoAnswerReason.SOURCES_DO_NOT_ANSWER,
                incomplete_sources=_incomplete_sources(shown),
            ),
            prompt=prompt,
            model=model,
        )

    invalid = 0
    citations_by_version: dict[uuid.UUID, list[Citation]] = {}

    for selection in result.quotes:
        index = selection.source - 1  # the model sees 1-based markers
        if not 0 <= index < len(shown):
            invalid += 1
            continue

        hit = shown[index]
        citations_by_version.setdefault(hit.document_version_id, []).append(
            Citation(
                chunk_id=hit.chunk_id,
                document_version_id=hit.document_version_id,
                document_title=hit.document_title,
                issuing_org=hit.issuing_org,
                version_label=hit.version_label,
                page_start=hit.page_start,
                page_end=hit.page_end,
                section=hit.section,
                quote=selection.quote,
            )
        )

    if not citations_by_version:
        return Answer(
            payload=AnswerPayload(
                query_language=query_language,
                no_answer_reason=NoAnswerReason.SOURCES_DO_NOT_ANSWER,
            ),
            prompt=prompt,
            model=model,
            invalid_sources=invalid,
        )

    # Ordered by retrieval, via the same grouping #22/#23 use — so what the clinician
    # reads and what the escalation rule sees are the same structure.
    groups = [
        SourceGroup(
            issuing_org=group.issuing_org,
            version_label=group.version_label,
            document_version_id=group.document_version_id,
            citations=citations_by_version[group.document_version_id],
            is_superseded=group.is_superseded,
            superseding_version_label=group.superseding_version_label,
            unreadable_pages=group.unreadable_pages or [],
        )
        for group in group_hits(shown)
        if group.document_version_id in citations_by_version
    ]

    return Answer(
        payload=AnswerPayload(query_language=query_language, groups=groups),
        prompt=prompt,
        model=model,
        invalid_sources=invalid,
    )
