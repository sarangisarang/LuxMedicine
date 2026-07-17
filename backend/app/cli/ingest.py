"""Put a PDF into the corpus: store it, register it, index it.

The HTTP endpoint (`POST /documents/versions`) registers a version and stops — indexing is
a separate, slower step, so an upload leaves a *pending* version that retrieval cannot see.
That is the right split for an API, and it leaves no way to get a guideline all the way in
from a terminal. This is that way.

    python -m app.cli.ingest storage/nhlbi_epr3_asthma_2007.pdf \
        --title "NHLBI EPR-3 Asthma Guidelines" --org NHLBI --version-label 2007 \
        --published 2007-08-28

**Public by default, and it says so.** `--clinic` makes the document private to one clinic
(#31); without it the document is a published guideline every clinic can retrieve. The flag
is spelled out rather than defaulted silently because "did you mean this to be public?" is
not a question to answer by omission.

Embedding runs locally (multilingual-e5-large, CPU) and costs no API quota — a 440-page
guideline takes minutes and no money. Nothing here calls the extractor.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.vocabulary import IssuingOrg
from app.services import storage
from app.services.indexing import index_version
from app.services.ingestion import (
    DuplicateFileError,
    RegistrationRequest,
    VersionLabelConflictError,
    register_version,
)

_READ_CHUNK = 1024 * 1024


def _hash_file(path: Path) -> str:
    """Streamed, not read whole: a 440-page guideline should not have to fit in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


async def ingest(
    pdf: Path,
    *,
    title: str,
    org: IssuingOrg,
    version_label: str,
    published_at: date | None,
    guideline_type: str | None,
    clinic_id: str | None,
) -> None:
    if not pdf.is_file():
        raise SystemExit(f"no such file: {pdf}")
    if pdf.read_bytes()[:5] != storage.PDF_MAGIC:
        raise SystemExit(f"not a PDF: {pdf}")

    settings = get_settings()
    file_hash = _hash_file(pdf)
    final = storage.path_for(file_hash, root=settings.storage_root)

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            try:
                version = await register_version(
                    session,
                    RegistrationRequest(
                        title=title,
                        issuing_org=org,
                        version_label=version_label,
                        file_hash=file_hash,
                        # Relative to the root (0014), so the row means the same thing to a
                        # reader on this host and to one in a container.
                        storage_uri=storage.relative_uri(final, root=settings.storage_root),
                        guideline_type=guideline_type,
                        published_at=published_at,
                        clinic_id=clinic_id,
                    ),
                )
            except DuplicateFileError as exc:
                raise SystemExit(
                    f"these exact bytes are already ingested as version "
                    f"{exc.existing.version_label!r} ({exc.existing.id})"
                ) from exc
            except VersionLabelConflictError as exc:
                raise SystemExit(
                    f"version {exc.version_label!r} already exists for this guideline with "
                    "different content — a revision needs its own label"
                ) from exc

            version_id = version.id

            # The row is about to commit, so the bytes go in now — the store never holds a
            # PDF no version points at, and no version points at a PDF that is not there.
            final.parent.mkdir(parents=True, exist_ok=True)
            if not final.exists():
                shutil.copy2(pdf, final)
            await session.commit()
            print(f"registered {title!r} {version_label} as {version_id} (pending)")

        async with maker() as session:
            from app.services.embedding import E5Embedder

            print("loading the embedder (local, no API quota)…")
            embedder = E5Embedder()
            print("extracting, chunking, embedding — minutes for a long guideline…")
            result = await index_version(session, version_id, embedder)
            await session.commit()

        print(
            f"indexed: {result.chunks_written} chunks over {result.pages} pages — "
            "version is now active"
        )
        # #41, surfaced rather than swallowed: a corpus with forty dropped chunks looks
        # exactly like one with none from the outside, it just quietly has less in it.
        if result.rejected_chunks:
            print(
                f"  {result.rejected_chunks} chunk(s) dropped for unresolved glyphs; "
                f"damaged pages: {result.damaged_pages}"
            )
        if result.empty_pages:
            print(f"  {len(result.empty_pages)} page(s) had no text layer")
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--org", required=True, choices=[o.value for o in IssuingOrg])
    parser.add_argument("--version-label", required=True)
    parser.add_argument("--published", type=date.fromisoformat, default=None)
    parser.add_argument("--guideline-type", default=None)
    parser.add_argument(
        "--clinic",
        default=None,
        help="make this document private to one clinic (#31); omit for a published guideline",
    )
    args = parser.parse_args(argv)

    asyncio.run(
        ingest(
            args.pdf,
            title=args.title,
            org=IssuingOrg(args.org),
            version_label=args.version_label,
            published_at=args.published,
            guideline_type=args.guideline_type,
            clinic_id=args.clinic,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
