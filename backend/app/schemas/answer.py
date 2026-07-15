"""The shape of an answer — and the place the MDR positioning is actually enforced.

LuxMedicine is a Clinical Search & Retrieval Engine, not a decision-support device.
The distinction is not a disclaimer in the UI; it is this schema. There is no field
for a recommendation, an assessment, or a suggested action. The only clinical text
that can leave the system is `quote` — a verbatim span from a source page — carried
by a Citation that names the edition and page it came from.

If a future requirement cannot be expressed here without adding "what the system
advises", that is the signal to stop and re-open the regulatory question, not to
widen the schema.
"""

import uuid

from pydantic import BaseModel, Field


class Citation(BaseModel):
    chunk_id: uuid.UUID
    document_version_id: uuid.UUID
    document_title: str
    issuing_org: str
    version_label: str

    # 1-based PDF page indices, not printed folios — see app/services/extraction.py.
    # A range because a chunk may cross a page break; equal values mean a single page.
    page_start: int
    page_end: int

    quote: str = Field(description="Verbatim span from the source. Never paraphrased.")

    @property
    def page_display(self) -> str:
        """"p. 45" or "pp. 45-46" — what a clinician is shown."""
        if self.page_start == self.page_end:
            return f"p. {self.page_start}"
        return f"pp. {self.page_start}-{self.page_end}"


class ExtractedStatement(BaseModel):
    """One claim lifted from the sources. `text` must be supported by `citations`;
    a statement with no citation is a bug, not a fallback.
    """

    text: str
    citations: list[Citation] = Field(min_length=1)


class SourceGroup(BaseModel):
    """Results grouped by issuing organisation, so a clinician sees *whose* guidance
    they are reading before they read it.
    """

    issuing_org: str
    version_label: str
    document_version_id: uuid.UUID
    statements: list[ExtractedStatement]

    # True when this version has a successor. Surfaced as: "a newer edition exists".
    is_superseded: bool = False
    superseding_version_label: str | None = None


class ConflictFinding(BaseModel):
    """Raised only when retrieval spans multiple issuing organisations and a
    comparison pass finds them genuinely divergent.

    Divergence is reported, never resolved: we show both passages and let the
    clinician judge. Picking a winner would be the advice we are avoiding.
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

    # Populated when the corpus cannot answer. An empty answer is a valid outcome;
    # a fabricated one is not.
    no_answer_reason: str | None = None
