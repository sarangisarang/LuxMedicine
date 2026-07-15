"""Marking one edition as replaced by another (#12).

This is what makes #17's warning possible: without a successor recorded, a clinician
reading the 2021 guideline is told nothing, and "no warning" is indistinguishable from
"this is current".

Three failure modes, handled at three different levels because they are decidable at
three different scopes:

- **Self-reference** — one row. A CHECK constraint (migration 0005).
- **Cross-document** — one row plus a join. A composite foreign key (migration 0005).
  The worst of the three: it would point a cardiologist at an oncology guideline, with
  a real version label attached.
- **Cycles** — the whole graph. Not expressible as a constraint, so it is prevented
  structurally here:

      An edition may only be superseded *by* an edition that has no successor of its
      own, and may not already have one itself.

  Every node has at most one out-edge. A cycle needs every node in it to have one, so
  closing a cycle means adding an edge into a node that already has an out-edge — which
  the rule forbids. Cycles of any length are unreachable, not merely unlikely.

  That check races, though: two transactions can each see a valid tail and together
  write `v1 -> v2` and `v2 -> v1`, both correct in isolation. So supersession takes an
  advisory lock on the document. Check-then-act is not atomic because the window is
  small — the same lesson as the audit chain and the ingestion gate.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentVersion, VersionStatus

# Namespaced so supersession contends only with itself, never with audit appends.
_SUPERSESSION_LOCK_NAMESPACE = 0x53555045  # "SUPE"


class SupersessionError(Exception):
    """Base for every refusal below."""


class NotSameDocumentError(SupersessionError):
    """Belt to the composite FK's braces: caught here with a legible message rather
    than surfacing as a foreign-key violation."""


class AlreadySupersededError(SupersessionError):
    """The predecessor already has a successor.

    Re-pointing it would drop an edition out of the chain: 2021 -> 2022 -> 2023 becomes
    2021 -> 2023, and 2022 quietly stops being reachable as anyone's successor.
    """

    def __init__(self, version_id: uuid.UUID, existing: uuid.UUID) -> None:
        super().__init__(f"version {version_id} is already superseded by {existing}")
        self.existing = existing


class SuccessorAlreadySupersededError(SupersessionError):
    """The proposed successor is itself out of date.

    This is the rule that makes cycles impossible. It also catches the honest mistake:
    marking 2021 as replaced by 2022 after 2023 already landed would leave the newest
    edition off the chain, and #17 would name 2022 as "the newer version" while 2023
    sat unmentioned.
    """

    def __init__(self, successor_id: uuid.UUID, its_successor: uuid.UUID) -> None:
        super().__init__(
            f"version {successor_id} cannot supersede anything: it is itself superseded "
            f"by {its_successor}"
        )
        self.its_successor = its_successor


class SupersededByPendingError(SupersessionError):
    """The successor is not indexed yet.

    Archiving the predecessor now would take the only searchable edition out of the
    corpus and replace it with one that has no chunks — retrieval would return nothing
    for the guideline, and #17 would point at a version that cannot be read.
    """


async def supersede(
    session: AsyncSession,
    *,
    version_id: uuid.UUID,
    superseded_by_id: uuid.UUID,
) -> DocumentVersion:
    """Mark `version_id` as replaced by `superseded_by_id`. Caller commits.

    The predecessor becomes ARCHIVED — never deleted. An answer given in 2024 must stay
    reproducible after the 2026 edition lands, which means its chunks and its PDF stay
    exactly where they were.
    """
    if version_id == superseded_by_id:
        raise SupersessionError("a version cannot supersede itself")

    version = (
        await session.execute(select(DocumentVersion).where(DocumentVersion.id == version_id))
    ).scalar_one()
    successor = (
        await session.execute(select(DocumentVersion).where(DocumentVersion.id == superseded_by_id))
    ).scalar_one()

    if version.document_id != successor.document_id:
        raise NotSameDocumentError(
            f"version {version_id} and {superseded_by_id} belong to different guidelines"
        )

    # Serialise per document. Without this, two supersessions can each observe a valid
    # tail and together close a cycle that neither could have created alone.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:document_id))"),
        {"namespace": _SUPERSESSION_LOCK_NAMESPACE, "document_id": str(version.document_id)},
    )

    # Re-read under the lock. The state checked before it was taken is not the state we
    # are about to write into.
    await session.refresh(version)
    await session.refresh(successor)

    if version.superseded_by is not None:
        raise AlreadySupersededError(version_id, version.superseded_by)

    if successor.superseded_by is not None:
        raise SuccessorAlreadySupersededError(superseded_by_id, successor.superseded_by)

    if successor.status is VersionStatus.PENDING:
        raise SupersededByPendingError(
            f"version {superseded_by_id} is still pending: index it before superseding "
            f"{version_id}, or the guideline becomes unsearchable"
        )

    version.superseded_by = superseded_by_id
    version.status = VersionStatus.ARCHIVED

    return version


async def latest_version(session: AsyncSession, document_id: uuid.UUID) -> DocumentVersion | None:
    """The tail of the chain: the edition nothing supersedes.

    Derived from the graph rather than from published_at on purpose. A "2023 Focused
    Update" is newer by date without replacing the 2021 guideline it amends — ordering
    by date would archive a document that is still current. Supersession is an editorial
    judgement, so it is recorded rather than inferred.
    """
    return (
        await session.execute(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.superseded_by.is_(None),
                DocumentVersion.status == VersionStatus.ACTIVE,
            )
        )
    ).scalars().first()
