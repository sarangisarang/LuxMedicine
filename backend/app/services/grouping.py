"""Grouping retrieved chunks by their source, and deciding when to compare (#22, #23).

Two jobs that look like one.

**Grouping** (#22) is for the clinician: whose guidance is this, and which edition. It
happens on every query and costs nothing.

**Escalation** (#23) decides whether a comparison pass is worth running at all. Asking a
model to "find conflicts" in a single organisation's guidance invents them, and the call
costs money on every query for a case that cannot arise. So it only fires when a result
set genuinely spans more than one issuing body.

The rule has a wrinkle the roadmap did not anticipate, and it matters more than the rule
itself: **a superseded edition is not a dissenting voice.** With archived versions in
scope, ESC 2021 saying 20 mg and ESC 2023 saying 35 mg is not a disagreement — it is an
update, which is precisely what supersession records. Comparing them would report every
revision in the corpus as a contradiction between an organisation and itself. So
escalation looks only at editions nothing supersedes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.services.retrieval import SearchHit


@dataclass(frozen=True)
class RetrievalGroup:
    """One edition's hits, in retrieval order.

    Keyed on the version rather than the organisation: ESC may publish both a heart
    failure and a hypertension guideline, and a clinician needs to know which one they
    are reading. Escalation counts organisations; presentation shows editions.
    """

    issuing_org: str
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    version_label: str
    document_title: str
    is_superseded: bool
    superseding_version_label: str | None
    hits: list[SearchHit]

    @property
    def best_rank(self) -> float:
        """Distance of this group's strongest hit — what orders the groups."""
        return min(hit.distance for hit in self.hits)


def group_hits(hits: list[SearchHit]) -> list[RetrievalGroup]:
    """Group hits by edition, preserving retrieval order within and between groups.

    Order is by each group's best hit, not by organisation name: a clinician reads
    downward, and the strongest match should be the first thing they see.
    """
    by_version: dict[uuid.UUID, list[SearchHit]] = {}
    for hit in hits:
        by_version.setdefault(hit.document_version_id, []).append(hit)

    groups = [
        RetrievalGroup(
            issuing_org=version_hits[0].issuing_org,
            document_id=version_hits[0].document_id,
            document_version_id=version_id,
            version_label=version_hits[0].version_label,
            document_title=version_hits[0].document_title,
            is_superseded=version_hits[0].is_superseded,
            superseding_version_label=version_hits[0].superseding_version_label,
            hits=version_hits,
        )
        for version_id, version_hits in by_version.items()
    ]

    return sorted(groups, key=lambda group: group.best_rank)


def comparable_groups(groups: list[RetrievalGroup]) -> list[RetrievalGroup]:
    """The groups a comparison pass may look at: current editions only.

    A superseded edition is history, deliberately retrieved (#14's include_archived). It
    is not a party to a disagreement, and treating it as one would turn every guideline
    update in the corpus into a reported contradiction.
    """
    return [group for group in groups if not group.is_superseded]


def distinct_organisations(groups: list[RetrievalGroup]) -> set[str]:
    return {group.issuing_org for group in comparable_groups(groups)}


def should_compare(groups: list[RetrievalGroup]) -> bool:
    """Whether a comparison pass (#24) is worth running.

    Only when current editions from more than one issuing body are in scope. Two ESC
    guidelines do not escalate — the same body is presumed to harmonise its own
    documents, and that presumption is the conservative one: a false conflict teaches
    clinicians to ignore the banner, and then it is not there on the day it matters
    (#25).

    **Known limit:** one organisation *can* contradict itself across two of its own
    guidelines, and this will not catch it. That is a real gap, accepted on the grounds
    that its prior is far lower than a cross-body disagreement and the cost of being
    wrong is alert fatigue. Revisit at #24 with real guidelines in hand, not before.
    """
    return len(distinct_organisations(groups)) > 1
