"""Content-addressed PDF storage.

Files live at `<root>/<hash[:2]>/<hash>.pdf`. The path derives from the content, so
identical bytes cannot occupy two locations and the store cannot disagree with
`document_versions.file_hash` about what a version contains.

Local disk for now. The interface is deliberately narrow (stage, commit, path_for) so
that swapping in EU-hosted object storage later touches this file only — and it will
have to be EU-hosted, for the same data-residency reason that decided #6.
"""

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import UploadFile

# Streamed rather than read whole: a 300-page guideline is large, and an upload sized
# to exhaust memory should not be able to.
_READ_CHUNK = 1024 * 1024

PDF_MAGIC = b"%PDF-"


class NotAPdfError(Exception):
    """The bytes are not a PDF, whatever the client claimed."""


class UploadTooLargeError(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"upload exceeds the {limit} byte limit")
        self.limit = limit


@dataclass(frozen=True)
class StagedUpload:
    file_hash: str
    size: int
    temp_path: Path


async def stage_upload(upload: UploadFile, *, max_bytes: int) -> StagedUpload:
    """Stream to a temp file, hashing as we go.

    Hash and write in one pass: reading twice would let the bytes change underneath us
    between hashing and storing, which is precisely the gap `file_hash` exists to close.

    The caller must commit_staged() or discard_staged() the result.
    """
    digest = hashlib.sha256()
    size = 0

    fd, temp_name = tempfile.mkstemp(suffix=".pdf.part")
    temp_path = Path(temp_name)

    try:
        with open(fd, "wb") as tmp:
            first = True
            while data := await upload.read(_READ_CHUNK):
                if first:
                    # Content-Type is whatever the client typed. The magic bytes are
                    # the file. Checked on the first chunk so a 90 MB non-PDF is
                    # rejected after 1 MB rather than after all of it.
                    if not data.startswith(PDF_MAGIC):
                        raise NotAPdfError("file does not begin with %PDF-")
                    first = False

                size += len(data)
                if size > max_bytes:
                    raise UploadTooLargeError(max_bytes)

                digest.update(data)
                tmp.write(data)

        if size == 0:
            raise NotAPdfError("file is empty")
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return StagedUpload(file_hash=digest.hexdigest(), size=size, temp_path=temp_path)


def path_for(file_hash: str, *, root: Path) -> Path:
    """Content-addressed location. Sharded by prefix — some filesystems degrade badly
    with tens of thousands of entries in one directory."""
    return root / file_hash[:2] / f"{file_hash}.pdf"


def relative_uri(path: Path, *, root: Path) -> str:
    """What goes in `document_versions.storage_uri`: the location *relative to the root*.

    An absolute path is not a location, it is a location on one machine. Storing one made
    the database non-portable in a way nothing noticed until the API ran in a container:
    every row said `C:\\Users\\...\\storage\\x.pdf`, the container mounts the same bytes at
    `/app/storage/x.pdf`, and every source PDF 500'd. The row is the same row; only the
    machine reading it changed, and that must not matter.

    Forward slashes always, so a URI written on Windows resolves on Linux.
    """
    return path.relative_to(root).as_posix()


def resolve(storage_uri: str, *, root: Path) -> Path:
    """Where a version's bytes actually are, for whoever is asking now.

    Tolerates a legacy absolute URI (rows written before `relative_uri` existed): if it
    names a file that exists, use it; otherwise fall back to the path under the current
    root, which is what a container or a second machine needs. Migration 0013 rewrites the
    stored values, so this fallback is for a database that has not been migrated yet —
    not a licence to keep writing absolute paths.
    """
    candidate = Path(storage_uri)
    # Absoluteness is OS-flavoured, and that is the whole trap this function exists for: a value
    # written on a Windows laptop (`C:\Users\...`) is NOT absolute to a Linux container's PosixPath,
    # so `candidate.is_absolute()` alone would let it fall through and be joined onto the root
    # verbatim — reproducing the very cross-machine 500 we are trying to tolerate. Recognise a
    # foreign absolute path under either flavour, regardless of the OS doing the reading.
    looks_absolute = (
        candidate.is_absolute()
        or PureWindowsPath(storage_uri).is_absolute()
        or PurePosixPath(storage_uri).is_absolute()
    )
    if looks_absolute:
        if candidate.is_file():
            return candidate
        # Written on another machine. Everything after the root's name is still the layout.
        # Split on both separators by hand — `candidate.as_posix()` on Linux leaves the Windows
        # backslashes untouched, so a `\`-only path would otherwise be one indivisible segment.
        parts = storage_uri.replace("\\", "/").split("/")
        if root.name in parts:
            tail = parts[parts.index(root.name) + 1 :]
            if tail:
                return root.joinpath(*tail)
        return root / parts[-1]
    return root / candidate


def commit_staged(staged: StagedUpload, *, root: Path) -> Path:
    """Move the staged file into the store and return its final path.

    Idempotent: if the content is already stored, the existing copy wins and the temp
    file is dropped. Identical hash means identical bytes, so there is nothing to
    choose between them.
    """
    final = path_for(staged.file_hash, root=root)
    final.parent.mkdir(parents=True, exist_ok=True)

    if final.exists():
        staged.temp_path.unlink(missing_ok=True)
        return final

    # shutil.move, not Path.rename: the temp dir is often on another filesystem.
    shutil.move(str(staged.temp_path), str(final))
    return final


def discard_staged(staged: StagedUpload) -> None:
    staged.temp_path.unlink(missing_ok=True)
