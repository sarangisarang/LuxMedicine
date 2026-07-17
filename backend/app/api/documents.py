"""Ingestion endpoint — the corpus's front door (#7)."""

import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.vocabulary import IssuingOrg
from app.db.session import get_session, get_tenant_session
from app.models.document import DocumentVersion, VersionStatus
from app.services import storage
from app.services.ingestion import (
    DuplicateFileError,
    RegistrationRequest,
    VersionLabelConflictError,
    register_version,
)

router = APIRouter(prefix="/documents", tags=["documents"])
settings = get_settings()


class VersionCreated(BaseModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    title: str
    issuing_org: IssuingOrg
    version_label: str
    file_hash: str
    size_bytes: int

    # Always "pending" here. Upload registers; indexing (#10) is a separate, slower step
    # and only it can make a version active. Reported so a caller is never left assuming
    # an accepted upload is a searchable one.
    status: VersionStatus


@router.post(
    "/versions",
    response_model=VersionCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a guideline PDF",
    responses={
        409: {"description": "These bytes are already ingested, or the version label is taken"},
        413: {"description": "Upload exceeds the size limit"},
        415: {"description": "Not a PDF"},
    },
)
async def create_version(
    file: UploadFile = File(...),
    title: str = Form(..., max_length=512),
    issuing_org: IssuingOrg = Form(...),
    version_label: str = Form(..., max_length=64),
    guideline_type: str | None = Form(None, max_length=128),
    published_at: date | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> VersionCreated:
    """Hash, deduplicate, store, register.

    Order matters. The bytes are hashed and staged to a temp file before anything
    touches the database, so a rejected upload leaves no row behind — and the file only
    enters the store once the row is safely committed, so the store never holds a PDF
    that no version points at.
    """
    try:
        staged = await storage.stage_upload(file, max_bytes=settings.max_upload_bytes)
    except storage.NotAPdfError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except storage.UploadTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc

    try:
        final_path = storage.path_for(staged.file_hash, root=settings.storage_root)

        version = await register_version(
            session,
            RegistrationRequest(
                title=title,
                issuing_org=issuing_org,
                version_label=version_label,
                file_hash=staged.file_hash,
                # Relative to the storage root, never absolute: the row must mean the same
                # thing to the API on this host and to the API in a container.
                storage_uri=storage.relative_uri(final_path, root=settings.storage_root),
                guideline_type=guideline_type,
                published_at=published_at,
            ),
        )
        document_id = version.document_id
        version_id = version.id
        version_status = version.status

        # Only now, with the row about to commit, does the file enter the store.
        storage.commit_staged(staged, root=settings.storage_root)
        await session.commit()

    except DuplicateFileError as exc:
        storage.discard_staged(staged)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "reason": "duplicate_file",
                "message": "This exact PDF is already in the corpus.",
                "file_hash": staged.file_hash,
                "existing_version_id": str(exc.existing.id),
                "existing_version_label": exc.existing.version_label,
            },
        ) from exc

    except VersionLabelConflictError as exc:
        storage.discard_staged(staged)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "reason": "version_label_taken",
                "message": (
                    f"Version {exc.version_label!r} already exists for this guideline with "
                    "different content. A revision needs its own label — reusing one would "
                    "silently change what an earlier answer cited."
                ),
                "document_id": str(exc.document_id),
            },
        ) from exc

    except BaseException:
        storage.discard_staged(staged)
        raise

    return VersionCreated(
        document_id=document_id,
        document_version_id=version_id,
        title=title,
        issuing_org=issuing_org,
        version_label=version_label,
        file_hash=staged.file_hash,
        size_bytes=staged.size,
        status=version_status,
    )


@router.get(
    "/{version_id}/pdf",
    summary="Stream a version's source PDF",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The source PDF"},
        404: {"description": "No such version, or not visible to this clinic"},
    },
)
async def get_version_pdf(
    version_id: uuid.UUID,
    session: AsyncSession = Depends(get_tenant_session),
) -> FileResponse:
    """The original PDF a citation points at, so a clinician can verify us against the
    source (#35). Verification a doctor will not actually perform is not verification, so
    this exists to make it one click.

    **Tenancy is the same one row-level security enforces everywhere, not a second copy of
    it.** The file store is content-addressed by hash and has no notion of a clinic — two
    clinics that upload the same bytes share one physical file. So access is decided at the
    *row*, not the file: this reads the version under `get_tenant_session`, and RLS returns
    it only if its document is visible (a published guideline with clinic_id NULL, or this
    clinic's own upload). A version another clinic owns is filtered out and reads exactly
    like one that does not exist — a 404 either way, so the endpoint never confirms that a
    document exists in a clinic the caller cannot see.

    The client passes a `version_id`, never a path or hash, so there is nothing to traverse;
    the path served is the one the row already holds. `FileResponse` streams from disk and
    honours Range requests, so a 100MB guideline never lands in memory and a PDF viewer can
    fetch the pages it needs.
    """
    version = (
        await session.execute(
            select(DocumentVersion).where(DocumentVersion.id == version_id)
        )
    ).scalar_one_or_none()

    # None means "RLS filtered it" or "it never existed" — deliberately indistinguishable.
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such document version")

    path = storage.resolve(version.storage_uri, root=settings.storage_root)
    if not path.is_file():
        # The row is visible but its bytes are gone from the store. That is our fault, not
        # a missing document — and reporting it as a 404 would send a clinician looking for
        # a guideline that we lost rather than one that never existed.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "the version exists but its stored file is missing",
        )

    return FileResponse(
        path,
        media_type="application/pdf",
        # Inline, not attachment: the point is to view the page in place, not download it.
        content_disposition_type="inline",
        filename=f"{version_id}.pdf",
    )
