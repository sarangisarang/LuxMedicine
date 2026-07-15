"""Ingestion endpoint — the corpus's front door (#7)."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.vocabulary import IssuingOrg
from app.db.session import get_session
from app.models.document import VersionStatus
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
                storage_uri=str(final_path),
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
