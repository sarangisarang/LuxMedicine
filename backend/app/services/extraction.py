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

from app.services.columns import find_regions
from app.services.table_extraction import self_describing_lines

# Pages are joined by a blank line. It belongs to no page: a chunk boundary landing in
# the gap resolves to the page before it, which is where its text actually came from.
PAGE_SEPARATOR = "\n\n"
LINE_SEPARATOR = "\n"

# How wide a gap between two glyphs has to be before it counts as a space (#43).
#
# PDFs frequently carry no space characters at all: words are separated by glyph
# positioning, and the extractor infers a space when the gap exceeds this. pdfplumber
# defaults to 3.0, which is too wide for KDIGO's typeface — so it inferred nothing and
# glued. 4.8% of the corpus's tokens came back over 20 characters long, the worst at 117:
#
#   'Geneticdiseasesarenotconsideredseparatelybecausesomediseasesineachcategoryarenowrecognized'
#
# **The bug that made this matter.** Asked when to refer a CKD patient, the model quoted
# p84 correctly and #19 rejected it — because p84's chunk said
# "anindicationforreferraloncepotentially" and the model, reading it, restored the spaces
# a human would. normalise() collapses whitespace runs; it cannot insert whitespace that
# was never there. The model was punished for being legible, and I diagnosed it as a
# hallucination, then a paraphrase, then a synthesis, before reading the PDF.
#
# 2.0 is measured, not chosen for sounding safe. It is the minimum of the curve: the only
# value with zero glued tokens *and* the fewest fragments (1-2 letter non-words, the
# signature of a word torn in half). Below it, over-splitting begins — the same damage in
# the other direction.
#
#     x_tol   glued   fragments
#      3.0     115      3.1%     <- pdfplumber's default
#      2.0       0      2.8%     <- here
#      1.0       0      3.5%
#
# Per-document tuning is the obvious next thought, and is refused for the reason
# body_font_size self-calibrates: a threshold that needs tuning is wrong on the document
# nobody tested. If another publisher's typeface glues at 2.0, that is a measurement to
# make, not a knob to expose.
X_TOLERANCE = 2.0

# When a page is *this* full of sideways characters, it is a landscape table printed with
# the text rotated 90 degrees, and pdfplumber reads rotated glyphs in reversed visual
# order (#43 follow-up). Measured on KDIGO: 10 of 163 pages are 97-99% rotated characters
# and come out backwards — 'yassadnanoitarbilacrCS' is 'SCr calibration and assay' read
# right to left. The other 153 pages are below 10%.
#
# Detected from the characters, not from page.rotation (empty here — the page is upright,
# only its content is turned) and not from width>height (these pages are portrait; the
# table was rotated, not the sheet). Both proxies missed all ten; upright=False caught all
# ten with nothing spurious. The signal has to be the thing that is actually wrong, which
# is the glyphs' orientation.
ROTATED_PAGE_SHARE = 0.5



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

    # Pages whose characters are mostly rotated — landscape tables read backwards
    # (#43 follow-up). Held out of the corpus like glyph-damaged chunks, and surfaced on
    # damaged_pages so a clinician is told this document has holes and where. A reversed
    # dosing table quoted verbatim passes #19 exactly as a broken formula does.
    rotated_pages: list[int] = field(default_factory=list)

    @property
    def damaged_pages(self) -> list[int]:
        return sorted({d.page for d in self.glyph_damage} | set(self.rotated_pages))

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


def _is_rotated(page) -> bool:
    """Whether the page's text is turned 90 degrees, so it would extract backwards.

    Measured from the characters' own orientation, because that is the thing that is
    wrong. page.rotation is empty on these pages (the sheet is upright, the table on it is
    turned) and they are portrait, so width>height misses them; both proxies flagged none
    of the ten real cases. `upright=False` flagged all ten and nothing else.
    """
    chars = [c for c in page.chars if c.get("text", "").strip()]
    if len(chars) < MIN_CHARS_FOR_ROTATION:
        return False
    sideways = sum(1 for c in chars if not c.get("upright", True))
    return sideways / len(chars) > ROTATED_PAGE_SHARE


# A near-empty page has no reading order to corrupt, and a stray rotated watermark on one
# should not condemn it. The threshold is a share; this stops it dividing by almost nothing.
MIN_CHARS_FOR_ROTATION = 50


def _lines_in_reading_order(page) -> list[dict]:
    """Lines the way a person reads them, not the way they sit on the y axis (#42).

    `extract_text_lines()` groups characters by vertical position. On a two-column page
    that welds the two columns: a line at y=400 in the left column and a different line at
    y=400 in the right column come back as one, and the result is fluent prose nobody
    wrote —

        "decreased GFR, possibly because BMI in isolation is a 'best practice' suggestion."

    — which #19 blesses, because the quote really is in the chunk. Measured on KDIGO 2012
    CKD: 117 of 163 pages two-column, 205 lines welding a hyphen-broken word to unrelated
    text. Cropping to each column first takes that to 53, a 74% reduction; the remainder
    are on pages find_gutter declines, where reading across is what happens today anyway.

    When there is no gutter this is exactly the old behaviour. That is the point: the
    decision to crop is per page, and refusing is the safe answer — bisecting a
    single-column page destroys every line on it, while missing a gutter leaves the page
    no worse than it is now.
    """
    tables = [table.bbox for table in page.find_tables()]
    regions = find_regions(
        page.chars,
        page_width=page.width,
        page_height=page.height,
        table_bboxes=tables,
    )

    if not any(region.is_two_column for region in regions):
        return page.extract_text_lines(return_chars=True, x_tolerance=X_TOLERANCE)

    lines: list[dict] = []
    for region in regions:
        # `filter`, not `crop`. crop() selects every object that *intersects* the box, so
        # a character whose box straddles a band boundary is handed to both bands and its
        # text lands in the corpus twice. Measured: 292 unresolved glyphs extracted where
        # page.chars has 278 — 14 read twice on three pages, and the second copy came back
        # as character-interleaved nonsense ("AIFbCbCr-eHvbiaAt1iocn"). filter() assigns
        # each character to exactly one band by where it starts.
        band = page.filter(
            lambda obj, r=region: r.top <= obj.get("top", -1) < r.bottom
        )
        if region.gutter is None:
            # A figure, a heading, a table across the page. Read across, in place.
            lines.extend(band.extract_text_lines(return_chars=True, x_tolerance=X_TOLERANCE))
            continue
        # Left column of this band, then right. Bands stay in page order, so a figure
        # between two column regions stays between them rather than migrating to the top.
        gutter_x = region.gutter.x
        left = band.filter(lambda obj, g=gutter_x: obj.get("x1", 0) <= g)
        right = band.filter(lambda obj, g=gutter_x: obj.get("x0", 0) >= g)
        lines.extend(left.extract_text_lines(return_chars=True, x_tolerance=X_TOLERANCE))
        lines.extend(right.extract_text_lines(return_chars=True, x_tolerance=X_TOLERANCE))
    return lines


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
    rotated_pages: list[int] = []
    all_char_sizes: list[float] = []
    cursor = 0

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)

        for index, page in enumerate(pdf.pages, start=1):
            if _is_rotated(page):
                # A landscape table turned 90 degrees. Its text extracts backwards, so it
                # is held out of the corpus rather than stored as a reversed string that
                # #19 would then bless. Recorded like an empty page: kept in the ledger
                # with a zero-width span so page numbering stays aligned, and reported on
                # damaged_pages so the clinician is told a page could not be read.
                rotated_pages.append(index)
                pages.append(PageText(number=index, text="", char_start=cursor, char_end=cursor))
                continue

            raw_lines = _lines_in_reading_order(page)
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

            # #48: append a self-describing line for each cleanly-mappable category-table row
            # ("ii. With aura — Cu-IUD: 1, ..., CHC: 4*"), so the table becomes answerable
            # instead of a headerless row the guard must refuse. Appended at the page's end,
            # inside its char span, so each resolves to this page; additive, never replacing
            # what was extracted, so a page with no clean table adds nothing. font_size 0 keeps
            # them body text (heading_of needs font_size > body), and they add no chars, so the
            # body-font measurement is untouched.
            for extra in self_describing_lines(page.extract_words()):
                extra = _normalise(extra)
                lines.append(
                    Line(
                        text=extra,
                        char_start=cursor,
                        char_end=cursor + len(extra),
                        page=index,
                        font_size=0.0,
                        is_bold=False,
                    )
                )
                page_line_texts.append(extra)
                cursor += len(extra) + len(LINE_SEPARATOR)

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
        rotated_pages=rotated_pages,
        body_font_size=_modal_size(all_char_sizes),
    )
