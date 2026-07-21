"""Registering an uploaded guideline.

The gate this implements is not "have we seen this file?" but "can this file enter the
corpus?". Ingesting the same guideline twice would double every passage in the vector
store, so retrieval would surface the same text as two independent sources — and #23
would then read one guideline as two organisations agreeing with themselves.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.vocabulary import IssuingOrg
from app.models.document import Document, DocumentVersion


class DuplicateFileError(Exception):
    """These exact bytes are already in the corpus."""

    def __init__(self, existing: DocumentVersion) -> None:
        super().__init__(f"file already ingested as version {existing.id}")
        self.existing = existing


class VersionLabelConflictError(Exception):
    """This document already has that version_label, carrying *different* bytes.

    Distinct from DuplicateFileError, and the distinction matters: identical bytes are
    a harmless re-upload, whereas different bytes under a label already in use means
    someone is revising history. Accepting it would break the promise that a 2024
    answer stays reproducible — the label would silently start resolving to new text.
    A revision needs its own label.
    """

    def __init__(self, document_id: uuid.UUID, version_label: str) -> None:
        super().__init__(f"version_label {version_label!r} already exists for document {document_id}")
        self.document_id = document_id
        self.version_label = version_label


@dataclass(frozen=True)
class RegistrationRequest:
    title: str
    issuing_org: IssuingOrg
    version_label: str
    file_hash: str
    storage_uri: str
    guideline_type: str | None = None
    published_at: date | None = None

    # None means a published guideline, visible to every clinic. A clinic id means an
    # upload that belongs to that clinic alone (#31). Required rather than defaulted at
    # the endpoint: "did you mean this to be public?" is not a question to answer by
    # omission.
    clinic_id: str | None = None


async def _find_by_hash(session: AsyncSession, file_hash: str) -> DocumentVersion | None:
    return (
        await session.execute(select(DocumentVersion).where(DocumentVersion.file_hash == file_hash))
    ).scalar_one_or_none()


async def _get_or_create_document(session: AsyncSession, req: RegistrationRequest) -> Document:
    """Get-or-create on the (issuing_org, title) natural key.

    ON CONFLICT DO NOTHING then SELECT, rather than SELECT then INSERT: the latter
    races two concurrent uploads of different editions of the same guideline into
    duplicate document rows, and the constraint would turn that into a 500 on a request
    that did nothing wrong.
    """
    await session.execute(
        pg_insert(Document)
        .values(
            id=uuid.uuid4(),
            title=req.title,
            issuing_org=str(req.issuing_org),
            clinic_id=req.clinic_id,
            # Derived, never supplied. Region describes the issuing body's
            # jurisdiction, not the individual document, so accepting it from the
            # caller only re-opens the inconsistency the IssuingOrg enum closes:
            # "EU" and "Europe" and "europe" for the same organisation.
            region=req.issuing_org.region,
            # Derived for the same reason region is, with more at stake: sector is a
            # retrieval boundary, so a caller able to set it is a caller able to file
            # HOAI as medical and have it quoted back to a clinician. There is no
            # field on RegistrationRequest to hold it — the same control the query
            # endpoint uses for actor_id.
            sector=str(req.issuing_org.sector),
            guideline_type=req.guideline_type,
        )
        .on_conflict_do_nothing(constraint="uq_document_clinic_org_title")
    )
    # Scoped to the clinic, or the lookup would hand clinic-b the Document row clinic-a
    # created for the same org+title — the two now coexist by design (0011), so a query
    # that ignores the clinic picks one of them arbitrarily.
    #
    # `IS NOT DISTINCT FROM`, not `==`: clinic_id is NULL for published guidelines, and
    # `clinic_id = NULL` is NULL, never true. That comparison would silently match no row
    # for every public document in the corpus.
    return (
        await session.execute(
            select(Document).where(
                Document.issuing_org == str(req.issuing_org),
                Document.title == req.title,
                Document.clinic_id.is_not_distinct_from(req.clinic_id),
            )
        )
    ).scalar_one()


async def register_version(session: AsyncSession, req: RegistrationRequest) -> DocumentVersion:
    """Register a new edition. Caller commits.

    Raises DuplicateFileError if the bytes are already in the corpus, or
    VersionLabelConflictError if the label is taken by different bytes.
    """
    if existing := await _find_by_hash(session, req.file_hash):
        raise DuplicateFileError(existing)

    document = await _get_or_create_document(session, req)
    # Read the id out before any flush that might roll back: rollback() expires ORM
    # instances, and touching document.id afterwards would attempt lazy IO from inside
    # the except block — surfacing as MissingGreenlet rather than as the conflict we
    # are trying to report.
    document_id = document.id

    version = DocumentVersion(
        document_id=document_id,
        version_label=req.version_label,
        published_at=req.published_at,
        file_hash=req.file_hash,
        storage_uri=req.storage_uri,
    )
    session.add(version)

    try:
        await session.flush()
    except IntegrityError as exc:
        # The pre-check above is an optimisation; this is the guarantee. Two concurrent
        # uploads of the same file both see no existing hash and both insert — the
        # database is what breaks the tie. Same lesson as the audit chain's advisory
        # lock: a check-then-act is not atomic just because the window is small.
        await session.rollback()
        constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", "") or str(exc.orig)

        if "file_hash" in constraint:
            winner = await _find_by_hash(session, req.file_hash)
            if winner is None:  # pragma: no cover — only if the winner rolled back too
                raise
            raise DuplicateFileError(winner) from exc

        if "uq_document_version" in constraint:
            raise VersionLabelConflictError(document_id, req.version_label) from exc

        raise

    return version
