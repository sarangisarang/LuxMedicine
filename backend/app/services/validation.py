"""Verbatim citation validation (#19).

The strongest anti-hallucination mechanism in this system, and it is a substring check.
No model call, no judgement, no threshold.

It works because the answer schema is extractive. A fabricated quote is *mechanically*
detectable: it will not appear in the chunk it claims to come from. Free-form generation
forfeits this entirely — there is nothing to compare a summary against. The MDR
positioning pays for itself technically here, not only legally.

**What it does not prove.** That the quote is in the chunk, and nothing more. It cannot
tell whether the chunk faithfully represents the PDF — a table mangled by extraction
produces a quote that passes this check and misleads anyway (#8, #13). Nor whether the
quote is lifted out of qualifying context (#38). #35's viewer is what closes those; this
closes the model inventing text outright.

Two normalisations are applied, and only two, each because it cannot change *which words
are quoted*:

- **Unicode NFC.** Extraction already normalises the corpus; a model may emit NFD. Same
  characters either way.
- **Whitespace runs collapse.** A quote spanning a line break comes back with the newline
  as a space, and the chunk has "\\n". Rejecting that would reject faithful quotes and
  teach whoever tunes the prompt to relax the check itself.

Nothing else. Not case — "MAY" and "may" differ in guideline language. Not punctuation —
a comma changes a dose list. Not ellipsis: a quote is contiguous, and "the dose is ...
contraindicated" would join two distant spans into a claim the source never makes. Two
spans are two citations.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from app.schemas.answer import AnswerPayload, Citation, SourceGroup

_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """NFC, collapsed whitespace, stripped. See the module docstring for why only these."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


@dataclass(frozen=True)
class RejectedCitation:
    """A quote that is not in the chunk it cites."""

    chunk_id: uuid.UUID
    document_version_id: uuid.UUID
    quote: str
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    payload: AnswerPayload
    rejected: list[RejectedCitation] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.rejected


def validate_answer(payload: AnswerPayload, chunks: dict[uuid.UUID, str]) -> ValidationResult:
    """Drop every citation whose quote is not verbatim in its chunk.

    `chunks` maps chunk_id to the exact stored text — the retrieved chunks, not a
    re-read. Fetching them fresh would validate against text that may have changed since
    retrieval, which is the wrong question.

    Per-citation rather than all-or-nothing. Each surviving quote is independently proven
    to be in its source, so discarding the rest with it would throw away real guidance
    over a neighbour's fault. The count travels back on the payload — a clinician seeing
    four quotes cannot tell a fifth was dropped, and a model fabricating text is not an
    event to hide.

    A group left with no citations is dropped too: an empty group is a heading with
    nothing under it.
    """
    rejected: list[RejectedCitation] = []
    surviving_groups: list[SourceGroup] = []

    for group in payload.groups:
        kept: list[Citation] = []

        for citation in group.citations:
            reason = _reject_reason(citation, chunks)
            if reason is None:
                kept.append(citation)
            else:
                rejected.append(
                    RejectedCitation(
                        chunk_id=citation.chunk_id,
                        document_version_id=citation.document_version_id,
                        quote=citation.quote,
                        reason=reason,
                    )
                )

        if kept:
            surviving_groups.append(group.model_copy(update={"citations": kept}))

    cleaned = payload.model_copy(
        update={"groups": surviving_groups, "rejected_citations": len(rejected)}
    )
    return ValidationResult(payload=cleaned, rejected=rejected)


def _reject_reason(citation: Citation, chunks: dict[uuid.UUID, str]) -> str | None:
    """None if the quote is verbatim in its chunk, else why it was refused."""
    source = chunks.get(citation.chunk_id)

    if source is None:
        # The model cited a chunk that was never retrieved. Not a quoting error — an
        # invented provenance, which is worse: the id would resolve to real text.
        return "cites a chunk that was not retrieved"

    quote = normalise(citation.quote)
    if not quote:
        return "quote is empty"

    if quote not in normalise(source):
        return "quote does not appear in the cited chunk"

    return None
