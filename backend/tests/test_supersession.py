"""Supersession integrity tests (#12).

Every one of these tries to break the chain. A supersession graph that only gets
well-behaved input proves nothing — the whole point is what it refuses.
"""

import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.supersession import (
    AlreadySupersededError,
    NotSameDocumentError,
    SupersededByPendingError,
    SupersessionError,
    SuccessorAlreadySupersededError,
    latest_version,
    supersede,
)


async def make_document(session: AsyncSession, *, org: str = "ESC") -> Document:
    document = Document(title=f"Guideline {uuid.uuid4().hex[:8]}", issuing_org=org, region="EU")
    session.add(document)
    await session.flush()
    return document


async def make_version(
    session: AsyncSession,
    document: Document,
    label: str,
    *,
    status: VersionStatus = VersionStatus.ACTIVE,
) -> DocumentVersion:
    marker = f"{document.id}-{label}-{uuid.uuid4().hex}"
    version = DocumentVersion(
        document_id=document.id,
        version_label=label,
        file_hash=hashlib.sha256(marker.encode()).hexdigest(),
        storage_uri=f"/store/{marker}.pdf",
        status=status,
    )
    session.add(version)
    await session.flush()
    return version


# --- the happy path ----------------------------------------------------------------


async def test_superseding_archives_the_predecessor_and_records_the_successor(session):
    document = await make_document(session)
    old = await make_version(session, document, "2021")
    new = await make_version(session, document, "2023")
    await session.commit()

    await supersede(session, version_id=old.id, superseded_by_id=new.id)
    await session.commit()

    assert old.superseded_by == new.id
    assert old.status is VersionStatus.ARCHIVED, "the predecessor must leave retrieval"
    assert new.status is VersionStatus.ACTIVE


async def test_the_archived_edition_keeps_its_chunks_and_its_pdf(session):
    """Archived, never deleted. An answer given in 2024 must stay reproducible after the
    2026 edition lands, which means its bytes stay exactly where they were."""
    document = await make_document(session)
    old = await make_version(session, document, "2021")
    new = await make_version(session, document, "2023")
    await session.commit()
    stored_uri, stored_hash = old.storage_uri, old.file_hash

    await supersede(session, version_id=old.id, superseded_by_id=new.id)
    await session.commit()

    assert old.storage_uri == stored_uri
    assert old.file_hash == stored_hash


async def test_latest_version_is_the_tail_of_the_chain(session):
    document = await make_document(session)
    v2021 = await make_version(session, document, "2021")
    v2022 = await make_version(session, document, "2022")
    v2023 = await make_version(session, document, "2023")
    await session.commit()

    await supersede(session, version_id=v2021.id, superseded_by_id=v2022.id)
    await supersede(session, version_id=v2022.id, superseded_by_id=v2023.id)
    await session.commit()

    tail = await latest_version(session, document.id)
    assert tail is not None
    assert tail.id == v2023.id


# --- cycles ------------------------------------------------------------------------


async def test_a_version_cannot_supersede_itself(session):
    document = await make_document(session)
    version = await make_version(session, document, "2021")
    await session.commit()

    with pytest.raises(SupersessionError):
        await supersede(session, version_id=version.id, superseded_by_id=version.id)


async def test_the_database_rejects_a_self_reference_written_directly(session):
    """The service refuses it, and so does the CHECK constraint — because the service is
    not the only thing that will ever write this column."""
    document = await make_document(session)
    version = await make_version(session, document, "2021")
    await session.commit()
    version_id = version.id

    with pytest.raises(DBAPIError) as exc:
        await session.execute(
            text("UPDATE document_versions SET superseded_by = id WHERE id = :id"),
            {"id": version_id},
        )
    assert "ck_version_not_self_superseding" in str(exc.value)
    await session.rollback()


async def test_a_two_cycle_is_impossible(session):
    """The structural rule, stated as a test.

    After v1 -> v2, closing the cycle means v2 -> v1 — and there v1 is the *successor*,
    so the rule that bites is "the successor must have no successor of its own". That
    is the rule doing the work: it forbids an edge into any node that already has an
    out-edge, which is exactly what closing a cycle requires.
    """
    document = await make_document(session)
    v1 = await make_version(session, document, "2021")
    v2 = await make_version(session, document, "2023")
    await session.commit()

    await supersede(session, version_id=v1.id, superseded_by_id=v2.id)
    await session.commit()

    with pytest.raises(SuccessorAlreadySupersededError):
        await supersede(session, version_id=v2.id, superseded_by_id=v1.id)


async def test_a_longer_cycle_is_impossible(session):
    """Every node has at most one out-edge, and closing a cycle means adding an edge
    into a node that already has one. Refused at the last link, for any length."""
    document = await make_document(session)
    v1 = await make_version(session, document, "2021")
    v2 = await make_version(session, document, "2022")
    v3 = await make_version(session, document, "2023")
    await session.commit()

    await supersede(session, version_id=v1.id, superseded_by_id=v2.id)
    await supersede(session, version_id=v2.id, superseded_by_id=v3.id)
    await session.commit()

    # v3 -> v1 would close 1 -> 2 -> 3 -> 1.
    with pytest.raises(SuccessorAlreadySupersededError):
        await supersede(session, version_id=v3.id, superseded_by_id=v1.id)


async def test_superseding_with_an_outdated_version_is_refused(session):
    """Not a cycle — an honest mistake with a quiet cost. Marking 2021 as replaced by
    2022 after 2023 exists would leave the newest edition off the chain, and #17 would
    announce 2022 as "the newer version" while 2023 sat unmentioned."""
    document = await make_document(session)
    v2021 = await make_version(session, document, "2021")
    v2022 = await make_version(session, document, "2022")
    v2023 = await make_version(session, document, "2023")
    await session.commit()

    await supersede(session, version_id=v2022.id, superseded_by_id=v2023.id)
    await session.commit()

    with pytest.raises(SuccessorAlreadySupersededError):
        await supersede(session, version_id=v2021.id, superseded_by_id=v2022.id)


async def test_repointing_an_already_superseded_version_is_refused(session):
    """Would drop an edition out of the chain: 2021 -> 2022 -> 2023 becomes 2021 -> 2023
    and 2022 quietly stops being anyone's successor."""
    document = await make_document(session)
    v2021 = await make_version(session, document, "2021")
    v2022 = await make_version(session, document, "2022")
    v2023 = await make_version(session, document, "2023")
    await session.commit()

    await supersede(session, version_id=v2021.id, superseded_by_id=v2022.id)
    await session.commit()

    with pytest.raises(AlreadySupersededError):
        await supersede(session, version_id=v2021.id, superseded_by_id=v2023.id)


# --- cross-document ----------------------------------------------------------------


async def test_superseding_across_guidelines_is_refused(session):
    """The most dangerous of the three, and the one that was not in the brief: #17 would
    tell a cardiologist their heart-failure guideline is superseded, and hand them an
    oncology document with a real version label on it."""
    cardiology = await make_document(session, org="ESC")
    oncology = await make_document(session, org="ESMO")
    heart = await make_version(session, cardiology, "2021")
    cancer = await make_version(session, oncology, "2023")
    await session.commit()

    with pytest.raises(NotSameDocumentError):
        await supersede(session, version_id=heart.id, superseded_by_id=cancer.id)


async def test_the_database_rejects_a_cross_document_reference_written_directly(session):
    """The composite foreign key, not the service, is the guarantee here."""
    cardiology = await make_document(session, org="AHA")
    oncology = await make_document(session, org="ASCO")
    heart = await make_version(session, cardiology, "2021")
    cancer = await make_version(session, oncology, "2023")
    await session.commit()
    heart_id, cancer_id = heart.id, cancer.id

    with pytest.raises(IntegrityError):
        await session.execute(
            text("UPDATE document_versions SET superseded_by = :other WHERE id = :id"),
            {"other": cancer_id, "id": heart_id},
        )
    await session.rollback()


# --- pending successor -------------------------------------------------------------


async def test_superseding_with_a_pending_version_is_refused(session):
    """Archiving the predecessor now would take the only searchable edition out of the
    corpus and put an unindexed one in its place — retrieval returns nothing for the
    guideline, and #17 points at a version that cannot be read."""
    document = await make_document(session)
    old = await make_version(session, document, "2021")
    new = await make_version(session, document, "2023", status=VersionStatus.PENDING)
    await session.commit()
    old_id = old.id  # the rollback below expires the instance

    with pytest.raises(SupersededByPendingError):
        await supersede(session, version_id=old_id, superseded_by_id=new.id)

    await session.rollback()
    refreshed = (
        await session.execute(select(DocumentVersion).where(DocumentVersion.id == old_id))
    ).scalar_one()
    assert refreshed.status is VersionStatus.ACTIVE, "the predecessor must stay searchable"


# --- concurrency -------------------------------------------------------------------


async def test_concurrent_supersessions_cannot_close_a_cycle(engine, session):
    """The reason supersede() takes an advisory lock.

    Without it, T1 sees v2 as a tail and T2 sees v1 as a tail — both true at the moment
    each looks. T1 writes v1 -> v2, T2 writes v2 -> v1, and neither did anything wrong.
    The cycle is created by the gap between checking and writing.
    """
    document = await make_document(session)
    v1 = await make_version(session, document, "2021")
    v2 = await make_version(session, document, "2023")
    await session.commit()
    v1_id, v2_id = v1.id, v2.id

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def attempt(first: uuid.UUID, second: uuid.UUID) -> str:
        async with maker() as s:
            try:
                await supersede(s, version_id=first, superseded_by_id=second)
                await s.commit()
                return "won"
            except SupersessionError:
                await s.rollback()
                return "refused"

    results = await asyncio.gather(attempt(v1_id, v2_id), attempt(v2_id, v1_id))

    assert sorted(results) == ["refused", "won"], f"got {results}"

    async with maker() as s:
        rows = (
            await s.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == document.id)
            )
        ).scalars().all()

    edges = {r.id: r.superseded_by for r in rows}
    assert not (edges[v1_id] == v2_id and edges[v2_id] == v1_id), "the chain closed a cycle"
