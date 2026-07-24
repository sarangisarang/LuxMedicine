"""Split an oversized PDF into page-bounded parts before ingestion.

The corpus was built by hand splitting long PDFs into parts (a 20th-edition commentary became
fourteen "(part NN)" documents). That was not busywork: a single 1,600-page version is one row
whose `unreadable_pages`, citations and retrieval all blur across the whole book, and a clinician
citing "page 12" cannot tell which of fourteen bound volumes it means. Parts keep each document at
a size where a page number is unambiguous and extraction damage is localised.

This is the only new mechanic the folder-upload needs; everything downstream (register, extract,
chunk, embed) already exists. Kept as a pure function over bytes — no filesystem, no DB — so the
split is testable on a PDF built in memory, and the upload path decides where the parts go.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader, PdfWriter

# 120: the size the corpus was hand-split at. A default, not a law — the caller may override, but
# above this a single document's pages stop being individually addressable in a citation.
DEFAULT_MAX_PAGES = 120


@dataclass(frozen=True)
class PdfPart:
    """One piece of a (possibly unsplit) PDF.

    `label_suffix` is "" when the document was small enough to leave whole — appended to the title
    so a part is named the way the hand-built ones are (" (part 01)"). `page_start`/`page_end` are
    1-based indices into the ORIGINAL document, so a part still knows where it came from.
    """

    label_suffix: str
    page_start: int
    page_end: int
    data: bytes


def page_count(pdf: bytes) -> int:
    return len(PdfReader(BytesIO(pdf)).pages)


def split_if_large(pdf: bytes, *, max_pages: int = DEFAULT_MAX_PAGES) -> list[PdfPart]:
    """Return the parts to ingest. A PDF of `max_pages` or fewer comes back as one whole part
    with an empty suffix; a larger one is cut into consecutive parts of at most `max_pages` each.

    The original bytes are returned unchanged for the unsplit case, so a document that did not need
    splitting hashes and stores identically to one ingested through the single-file path.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    reader = PdfReader(BytesIO(pdf))
    total = len(reader.pages)
    if total <= max_pages:
        return [PdfPart(label_suffix="", page_start=1, page_end=total, data=pdf)]

    part_count = math.ceil(total / max_pages)
    width = max(2, len(str(part_count)))  # (part 01) … (part 14); widen past 99 parts
    parts: list[PdfPart] = []
    for i in range(part_count):
        lo = i * max_pages
        hi = min(lo + max_pages, total)
        writer = PdfWriter()
        for page in reader.pages[lo:hi]:
            writer.add_page(page)
        buffer = BytesIO()
        writer.write(buffer)
        parts.append(
            PdfPart(
                label_suffix=f" (part {str(i + 1).zfill(width)})",
                page_start=lo + 1,
                page_end=hi,
                data=buffer.getvalue(),
            )
        )
    return parts
