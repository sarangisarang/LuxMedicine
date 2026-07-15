"""PDF text extraction that keeps page provenance and typography (#8).

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

Text is assembled from `extract_text_lines()` rather than `extract_text()` so that each
line carries its font size and weight. This is not decoration: it is the only signal
that separates a section heading from a dosage. `2.1 Pharmacological therapy` and
`2.5 mg may be used...` are indistinguishable to a regex — both open with a decimal at
the start of a line — and in a clinical corpus, decimals at the start of lines are
usually doses. Typography tells them apart; nothing else does. See chunking.py.

The two sources produce byte-identical text (asserted in tests), so building from lines
costs nothing: no re-ingestion, and no already-cited passage quietly changing shape.

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
from collections import Counter
import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

# Pages are joined by a blank line. It belongs to no page: a chunk boundary landing in
# the gap resolves to the page before it, which is where its text actually came from.
PAGE_SEPARATOR = "\n\n"
LINE_SEPARATOR = "\n"


# pdfplumber's placeholder for a glyph whose font declares no ToUnicode mapping. The PDF
# says "draw glyph N from this font" and never says which character N is, so the meaning
# is not in the text layer for anything to extract. Measured on KDIGO 2012 CKD (#41): 278
# of these across 18 of 163 pages, all from one embedded symbol font, and every one of
# them was a multiplication sign or a minus inside a dosing formula.
UNRESOLVED_GLYPH = re.compile(r"\(cid:\d+\)")


@dataclass(frozen=True)
class GlyphDamage:
    """Unresolved glyphs on one line, and where to find them."""

    page: int
    line_text: str
    count: int


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
class Line:
    """One line of text, with the typography needed to tell a heading from a dose."""

    text: str
    char_start: int
    char_end: int
    page: int

    # Modal size among the line's characters, not max: a superscript reference marker
    # would otherwise make an ordinary body line look like a heading.
    font_size: float
    is_bold: bool


@dataclass(frozen=True)
class PageText:
    number: int  # 1-based PDF page index
    text: str
    char_start: int
    char_end: int  # exclusive; excludes the separator that follows


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    pages: list[PageText]
    lines: list[Line]
    empty_pages: list[int]


    # Modal character size across the document — this document's body text.
    #
    # Self-calibrating on purpose. Absolute thresholds would need tuning per publisher
    # (ESC, AHA and NICE all typeset differently, and a clinic's Word export differs
    # again), and a threshold that needs tuning is a threshold that is wrong on the
    # document nobody tested. Every document declares its own baseline instead.
    body_font_size: float

    # Lines whose glyphs the font could not name. Reported rather than raised: 18 damaged
    # pages out of 163 is a real guideline that mostly extracted fine, and throwing it all
    # away would lose 145 good pages. The caller decides — but it cannot *not* be told,
    # which is the whole point (#41).
    #
    # The damage is invisible downstream: #19 checks the quote against the chunk, and the
    # chunk is what is wrong. A corrupted formula quoted character-for-character passes
    # every check the system has.
    glyph_damage: list[GlyphDamage] = field(default_factory=list)

    @property
    def damaged_pages(self) -> list[int]:
        return sorted({d.page for d in self.glyph_damage})

    def damage_ratio(self, page_count: int) -> float:
        return len(self.damaged_pages) / page_count if page_count else 0.0

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


def _modal_size(sizes: list[float]) -> float:
    return Counter(sizes).most_common(1)[0][0]


def extract_pdf(path: Path) -> ExtractedDocument:
    """Extract text line by line, recording each line's and page's offset as it goes.

    Raises NoTextLayerError if nothing is extractable.
    """
    page_texts: list[str] = []
    pages: list[PageText] = []
    lines: list[Line] = []
    empty_pages: list[int] = []
    glyph_damage: list[GlyphDamage] = []
    all_char_sizes: list[float] = []
    cursor = 0

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)

        for index, page in enumerate(pdf.pages, start=1):
            raw_lines = page.extract_text_lines(return_chars=True)
            page_line_texts = [_normalise(line["text"]) for line in raw_lines]

            if not any(text.strip() for text in page_line_texts):
                # Kept in the ledger with a zero-width span so that page numbering stays
                # aligned with the PDF: dropping a blank page would shift every later
                # citation by one.
                empty_pages.append(index)
                pages.append(PageText(number=index, text="", char_start=cursor, char_end=cursor))
                continue

            page_start = cursor
            for raw, text in zip(raw_lines, page_line_texts, strict=True):
                sizes = [round(char["size"], 1) for char in raw["chars"]]
                all_char_sizes.extend(sizes)

                unresolved = UNRESOLVED_GLYPH.findall(text)
                if unresolved:
                    glyph_damage.append(
                        GlyphDamage(page=index, line_text=text, count=len(unresolved))
                    )

                lines.append(
                    Line(
                        text=text,
                        char_start=cursor,
                        char_end=cursor + len(text),
                        page=index,
                        font_size=_modal_size(sizes) if sizes else 0.0,
                        is_bold=any("bold" in char["fontname"].lower() for char in raw["chars"]),
                    )
                )
                cursor += len(text) + len(LINE_SEPARATOR)

            # The trailing line separator is not part of the page; swap it for the page
            # separator so the two ledgers stay consistent with the assembled text.
            cursor -= len(LINE_SEPARATOR)
            page_text = LINE_SEPARATOR.join(page_line_texts)
            pages.append(
                PageText(number=index, text=page_text, char_start=page_start, char_end=cursor)
            )
            page_texts.append(page_text)
            cursor += len(PAGE_SEPARATOR)

    if not page_texts:
        raise NoTextLayerError(path, page_count)

    return ExtractedDocument(
        text=PAGE_SEPARATOR.join(page_texts),
        pages=pages,
        lines=lines,
        empty_pages=empty_pages,
        glyph_damage=glyph_damage,
        body_font_size=_modal_size(all_char_sizes),
    )
