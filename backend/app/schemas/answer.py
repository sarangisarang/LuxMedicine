"""The shape of an answer — and the place the MDR positioning is actually enforced.

LuxMedicine is a Clinical Search & Retrieval Engine, not a decision-support device.
The distinction is not a disclaimer in the UI; it is this schema. There is no field for
a recommendation, an assessment, or a suggested action. The only clinical text that can
leave the system is `Citation.quote` — a verbatim span from a source page, validated
character-for-character against the chunk it claims to come from (#19).

If a future requirement cannot be expressed here without adding "what the system
advises", that is the signal to stop and re-open the regulatory question, not to widen
the schema.

**This schema previously had a hole, and it is worth recording why.**

An earlier version carried `ExtractedStatement.text` — a model-written claim, with
citations attached as supporting evidence. #19 validates quotes. It never validated
`text`. So this passed every check:

    text  = "Enalapril is contraindicated in renal impairment"   <- invented
    quote = "The target dose of enalapril is 20 mg twice daily"   <- real, verbatim

Fabricated clinical advice, wearing a genuine citation. Worse than a bare hallucination,
because the citation is what makes it credible — and `text` is the headline a clinician
reads, with the quote as small print underneath.

So statements are gone. A source group carries citations, and a citation carries a
quote. There is no field left for the model to write a claim into, which also settles
#21 structurally: "a statement with no citation" is now unrepresentable rather than
merely forbidden.

What that costs is real and worth naming: nothing summarises, nothing paraphrases, and
a clinician reads the guideline's own words. For a retrieval engine that is the product,
not a limitation.
"""

import uuid
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class NoAnswerReason(StrEnum):
    """Why nothing is being shown — a closed set, not prose.

    Free text here would be the same hole that removed `ExtractedStatement.text`: a
    model asked to explain itself in an empty answer will write "no guidance found,
    though enalapril is generally used" — advice, smuggled through the one field that
    was meant to say there is none.

    An enum leaves nothing to write into. The wording a clinician reads lives in the UI,
    where a human chose it.

    The three are kept apart because conflating them tells a clinician the wrong thing.
    "The corpus has nothing on this" is a fact about the guidelines. "The answer could
    not be verified" is a fact about us malfunctioning — and reporting our own failure as
    an absence of guidance would be the quietest lie this system could tell.
    """

    # Retrieval returned nothing at all. The corpus does not cover the question.
    NO_RELEVANT_SOURCES = "no_relevant_sources"

    # Passages were retrieved and read; none of them answer the question.
    SOURCES_DO_NOT_ANSWER = "sources_do_not_answer"

    # Quotes came back and #19 rejected them. This is our fault, not the corpus's.
    VERIFICATION_FAILED = "verification_failed"


class Citation(BaseModel):
    """A verbatim span of a source, and exactly where it came from.

    `quote` is the only clinical text in this schema. Everything else is provenance.
    """

    chunk_id: uuid.UUID
    document_version_id: uuid.UUID
    document_title: str
    issuing_org: str
    version_label: str

    # 1-based PDF page indices, not printed folios — see app/services/extraction.py.
    # A range because a chunk may cross a page break; equal values mean a single page.
    page_start: int
    page_end: int

    # Where in the guideline this sits, when the document numbers its sections.
    # None is normal and honest — see chunking.py on why detection stays conservative.
    section: str | None = None

    quote: str = Field(
        min_length=1,
        description=(
            "A contiguous verbatim span of the cited chunk. Never paraphrased, never "
            "elided: two separate spans are two citations. Validated by #19 against the "
            "chunk text."
        ),
    )

    @property
    def page_display(self) -> str:
        """"p. 45" or "pp. 45-46" — what a clinician is shown."""
        if self.page_start == self.page_end:
            return f"p. {self.page_start}"
        return f"pp. {self.page_start}-{self.page_end}"


class SourceGroup(BaseModel):
    """What one issuing organisation's guideline says, in its own words.

    Grouped by organisation so a clinician sees *whose* guidance they are reading before
    they read it — and so #23 can tell whether a result set spans more than one body.
    """

    issuing_org: str
    version_label: str
    document_version_id: uuid.UUID

    # The answer itself. Quotes, in the order they were retrieved.
    citations: list[Citation] = Field(min_length=1)

    # True when this version has a successor. #17 resolves the chain to its tail, so the
    # label names the edition that is current — not the next one, which may also be
    # stale.
    is_superseded: bool = False
    superseding_version_label: str | None = None

    # Pages of this document whose text could not be read (#41). Not a diagnostic —
    # something the clinician is entitled to see next to the quote.
    #
    # KDIGO 2012 arrives with 18 such pages: the CKD-EPI equation extracted as
    # `141(cid:2)min(SCr/k,1)...`, every operator deleted, so those chunks are refused at
    # indexing. Without this field the answer is prose about eGFR with the equation
    # quietly absent, and it looks complete. "The guideline does not say" and "we could
    # not read the page where it says it" would be the same output — the confusion
    # NoAnswerReason exists to prevent, one layer down.
    #
    # This carries no clinical content and cannot: it is a list of page numbers. The MDR
    # line is untouched — nothing here advises, it only says where this document is
    # unreadable so a clinician can open the PDF and look.
    unreadable_pages: list[int] = Field(default_factory=list)

    @property
    def has_unreadable_pages(self) -> bool:
        return bool(self.unreadable_pages)


class ConflictFinding(BaseModel):
    """Raised only when retrieval spans multiple issuing organisations and a comparison
    pass finds them genuinely divergent.

    Divergence is reported, never resolved: we show both passages and let the clinician
    judge. Picking a winner would be the advice we are avoiding.

    **`description` is model prose and #19 does not validate it** — the same category of
    hole that removed `ExtractedStatement.text` above. It survives for now because it
    labels a view in which both quotes are the content, rather than replacing them the
    way a statement headline did. That is a UI assumption living in a schema, which is
    fragile. Settle it at #24, before a prompt is written that fills it.
    """

    topic: str
    description: str
    conflicting_group_ids: list[uuid.UUID] = Field(
        min_length=2, description="document_version_ids of the diverging sources"
    )


class AnswerPayload(BaseModel):
    """Persisted verbatim into audit_log.response."""

    query_language: str | None = None
    groups: list[SourceGroup] = Field(default_factory=list)
    conflicts: list[ConflictFinding] = Field(default_factory=list)

    # Why nothing is shown. An empty answer is a valid clinical outcome; a fabricated one
    # is a safety incident, so this path must be as easy to take as any other (#20).
    no_answer_reason: NoAnswerReason | None = None

    # Quotes rejected by #19 as not appearing in the chunk they cited. Reported, not
    # silently dropped: a clinician seeing four quotes cannot tell that a fifth was
    # discarded, and "the model fabricated something just now" is exactly the kind of
    # thing that must not be invisible.
    rejected_citations: int = 0

    @model_validator(mode="after")
    def an_empty_answer_must_say_why(self) -> "AnswerPayload":
        """No groups and no reason is unrepresentable.

        Otherwise the worst outcome is the easiest one to reach: a clinician is shown a
        blank result and cannot tell "the guidelines do not cover this" from "we broke".
        Every other silent-failure guard in this system exists for the same reason —
        pending versions (#10), scanned PDFs (#8), rejected quotes above.
        """
        if not self.groups and self.no_answer_reason is None:
            raise ValueError(
                "an answer with no groups must carry a no_answer_reason — a blank result "
                "with no explanation cannot be distinguished from a malfunction"
            )
        if not self.groups and self.conflicts:
            raise ValueError("conflicts reference groups; there are none")
        return self
