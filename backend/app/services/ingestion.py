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
    region: str | None = None
    guideline_type: str | None = None
    published_at: date | None = None


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
            region=req.region if req.region is not None else req.issuing_org.region,
            guideline_type=req.guideline_type,
        )
        .on_conflict_do_nothing(constraint="uq_document_org_title")
    )
    return (
        await session.execute(
            select(Document).where(
                Document.issuing_org == str(req.issuing_org), Document.title == req.title
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
