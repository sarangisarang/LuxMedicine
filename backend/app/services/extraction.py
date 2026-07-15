"""PDF text extraction that keeps page provenance (#8).

The design in one line: **record the page map while building the text, never search for
it afterwards.**

The tempting alternative is to concatenate the pages, chunk the result, then locate each
chunk with `text.find(chunk)` to recover its page. That is wrong in a way that passes
casual testing: running headers, repeated section titles, and boilerplate like "ESC
Guidelines" appear on every page, so `find` returns the first occurrence rather than the
right one, and the citation points at page 3 for text on page 47. Advancing a cursor
makes it wrong less often, not less wrongly.

Here every page's offset is recorded as the text is assembled, so resolving a span to
its pages is a bisect over known values — exact by construction, with nothing to search
and no failure mode to guard against.

**Page numbers are 1-based PDF indices, not printed folios.** A guideline with roman-
numeralled front matter prints "37" on its 45th sheet, and we would cite "p. 45". Within
our own system that is consistent — #35's viewer opens PDF page 45 and shows the quoted
text. Against a paper copy it is not. Fixing it means reading the printed folio off each
page and trusting it, which is its own guesswork; the honest move for now is to be exact
about a well-defined number and say which one it is.
"""

from __future__ import annotations

import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

# Pages are joined by a blank line. It belongs to no page: a chunk boundary landing in
# the gap resolves to the page before it, which is where its text actually came from.
PAGE_SEPARATOR = "\n\n"


class NoTextLayerError(Exception):
    """The PDF has no extractable text — it is almost certainly scanned images.

    Raised rather than returning empty text, because empty text ingests cleanly: a
    document with zero chunks enters the corpus, retrieval never returns it, and nobody
    is told. The clinician sees "no guidance found" and cannot tell that from "we never
    read your upload". This is the signal for #13.
    """

    def __init__(self, path: Path, page_count: int) -> None:
        super().__init__(
            f"{path.name}: no text layer across {page_count} pages — likely a scanned PDF (#13)"
        )
        self.page_count = page_count


@dataclass(frozen=True)
class PageText:
    """One page's text and its span within ExtractedDocument.text."""

    number: int  # 1-based PDF page index
    text: str
    char_start: int
    char_end: int  # exclusive; excludes the separator that follows


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    pages: list[PageText]
    empty_pages: list[int]

    def pages_for_span(self, start: int, end: int) -> tuple[int, int]:
        """Resolve a [start, end) span of `text` to the page range it covers.

        Bisect over recorded offsets, not a search through the text.
        """
        if not self.pages:  # pragma: no cover — extract_pdf raises before this
            raise ValueError("no pages")
        if start < 0 or end > len(self.text) or start >= end:
            raise ValueError(f"span [{start}, {end}) is outside the document")

        starts = [page.char_start for page in self.pages]

        first = max(bisect_right(starts, start) - 1, 0)
        # end is exclusive, so probe the last character actually covered. Without the
        # -1 a chunk ending exactly on a page boundary would claim the next page too.
        last = max(bisect_right(starts, end - 1) - 1, 0)

        return self.pages[first].number, self.pages[last].number


def _normalise(raw: str) -> str:
    """Unicode NFC and tidy line endings. Deliberately nothing more.

    Note what is *not* done here: de-hyphenation. Guidelines break words across lines
    ("hyper-\\ntension"), which will cost the lexical half of hybrid search (#15) real
    matches on exactly the high-stakes terms it exists to catch.

    It is left alone because the naive fix corrupts the corpus irreversibly. Joining on
    a trailing hyphen turns "anti-\\ninflammatory" into "antiinflammatory" and
    "COVID-\\n19" into "COVID19" — silently, in stored text that citations then quote
    and #19 then validates as faithful. Better to keep the text as extracted and handle
    hyphenation where it is a search problem (#15/#16), where a wrong guess costs a
    missed hit rather than a corrupted quote.
    """
    return unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")


def extract_pdf(path: Path, *, layout: bool = False) -> ExtractedDocument:
    """Extract text page by page, recording each page's offset as it goes.

    `layout=False` by default. layout=True preserves visual position by padding with
    spaces, which helps tables read correctly and makes prose ragged — and prose is
    almost all of a guideline. Tables come out imperfect either way: that is #13's
    problem to improve and #35's to make checkable, not something a flag fixes.

    Raises NoTextLayerError if nothing is extractable.
    """
    parts: list[str] = []
    pages: list[PageText] = []
    empty_pages: list[int] = []
    cursor = 0

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)

        for index, page in enumerate(pdf.pages, start=1):
            text = _normalise(page.extract_text(layout=layout) or "")

            if not text.strip():
                # Kept in the ledger with a zero-width span so that page numbering stays
                # aligned with the PDF: dropping a blank page would shift every later
                # citation by one.
                empty_pages.append(index)
                pages.append(PageText(number=index, text="", char_start=cursor, char_end=cursor))
                continue

            pages.append(
                PageText(
                    number=index,
                    text=text,
                    char_start=cursor,
                    char_end=cursor + len(text),
                )
            )
            parts.append(text)
            cursor += len(text) + len(PAGE_SEPARATOR)

    if not parts:
        raise NoTextLayerError(path, page_count)

    return ExtractedDocument(
        text=PAGE_SEPARATOR.join(parts),
        pages=pages,
        empty_pages=empty_pages,
    )
