"""Take a PDF (or an oversized one, split into parts) all the way into the corpus.

`app.cli.ingest` already does this for one file from the command line; the HTTP path stops at
`register_version` on purpose (indexing is slow and loads the embedder). The folder-upload needs
the whole thing behind an endpoint, so the CLI's flow is factored out here as a service the CLI, a
background job, and a test can all call — over bytes, not a Path, because an upload has no file on
disk yet.

`ingest_document` is the folder-upload's unit of work: split if large, then ingest each part as its
own version (title + " (part NN)"), exactly as the corpus was hand-built. `ingest_pdf` is the
single-part step, kept separate so a background job can report progress per part.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.vocabulary import IssuingOrg, LicenseStatus
from app.models.document import VersionStatus
from app.services import storage
from app.services.embedding import Embedder
from app.services.indexing import IndexResult, index_version
from app.services.ingestion import RegistrationRequest, register_version
from app.services.pdf_split import DEFAULT_MAX_PAGES, split_if_large


@dataclass(frozen=True)
class IngestedVersion:
    version_id: uuid.UUID
    title: str
    result: IndexResult
    # True when the licence was not affirmed: indexed, but left WITHDRAWN (quarantined) so it is not
    # retrievable until a human confirms the licence. See LicenseStatus.
    quarantined: bool = False


async def ingest_pdf(
    session: AsyncSession,
    *,
    pdf: bytes,
    title: str,
    org: IssuingOrg,
    version_label: str,
    embedder: Embedder,
    license_status: LicenseStatus = LicenseStatus.UNKNOWN,
    provenance_source: str | None = None,
    published_at: date | None = None,
    guideline_type: str | None = None,
    clinic_id: str | None = None,
) -> IngestedVersion:
    """Register, store, and index one PDF. Active on return only if the licence was affirmed.

    The store root is `settings.storage_root` and nothing else: `index_version` resolves the PDF
    against exactly that root, so a caller-chosen root would register a row indexing cannot read.
    Registration commits before indexing begins, exactly as the CLI does it: the store never holds
    a PDF no row points at, and a slow index does not sit inside the registration transaction. A
    `DuplicateFileError`/`VersionLabelConflictError` from registration and a
    `NoTextLayerError`/`EmptyExtractionError` from indexing both propagate — the caller decides
    whether one bad file in a folder fails the file or the batch.

    **The upload gate.** An unaffirmed licence (anything but public_domain/licensed) is still
    ingested — indexed, chunks and PDF in place — but left WITHDRAWN (quarantined), never retrievable,
    until a human confirms it. Fail-safe by construction: the copyrighted upload does not go live
    because a reviewer forgot; it does not go live unless someone said it may.
    """
    if pdf[:5] != storage.PDF_MAGIC:
        raise storage.NotAPdfError("file does not begin with %PDF-")

    storage_root = get_settings().storage_root
    file_hash = hashlib.sha256(pdf).hexdigest()
    final = storage.path_for(file_hash, root=storage_root)

    version = await register_version(
        session,
        RegistrationRequest(
            title=title,
            issuing_org=org,
            version_label=version_label,
            file_hash=file_hash,
            storage_uri=storage.relative_uri(final, root=storage_root),
            guideline_type=guideline_type,
            published_at=published_at,
            clinic_id=clinic_id,
            license_status=license_status,
            provenance_source=provenance_source,
        ),
    )

    # Bytes land before the commit, so the store and the row become true together (the same order
    # the CLI keeps). Content-addressed, so an identical file already present is left as-is.
    final.parent.mkdir(parents=True, exist_ok=True)
    if not final.exists():
        final.write_bytes(pdf)
    await session.commit()

    result = await index_version(session, version.id, embedder)

    # index_version activated the version; if the licence is not affirmed, withdraw it back into
    # quarantine before committing. The chunks stay (ready to activate once the licence is
    # confirmed); retrieval, which returns only `active`, never sees them.
    quarantined = not license_status.is_affirmed
    if quarantined:
        version.status = VersionStatus.WITHDRAWN
    await session.commit()
    return IngestedVersion(
        version_id=version.id, title=title, result=result, quarantined=quarantined
    )


async def ingest_document(
    session: AsyncSession,
    *,
    pdf: bytes,
    title: str,
    org: IssuingOrg,
    version_label: str,
    embedder: Embedder,
    license_status: LicenseStatus = LicenseStatus.UNKNOWN,
    provenance_source: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    published_at: date | None = None,
    guideline_type: str | None = None,
    clinic_id: str | None = None,
) -> list[IngestedVersion]:
    """Split an oversized PDF into parts and ingest each as its own version.

    A document at or under `max_pages` becomes one version with the plain title; a larger one
    becomes several, titled "<title> (part NN)". Returns one `IngestedVersion` per part, in order,
    so a caller reporting progress knows how many units of work the file became.
    """
    parts = split_if_large(pdf, max_pages=max_pages)
    ingested: list[IngestedVersion] = []
    for part in parts:
        ingested.append(
            await ingest_pdf(
                session,
                pdf=part.data,
                title=f"{title}{part.label_suffix}",
                org=org,
                version_label=version_label,
                embedder=embedder,
                license_status=license_status,
                provenance_source=provenance_source,
                published_at=published_at,
                guideline_type=guideline_type,
                clinic_id=clinic_id,
            )
        )
    return ingested
