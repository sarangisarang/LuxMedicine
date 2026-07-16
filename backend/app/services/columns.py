"""Finding the gutter, so columns are read down and not across (#42).

**What this exists to stop.** Reading a two-column page line by line welds the two columns
into sentences nobody wrote. Measured on KDIGO 2012 CKD: 117 of 163 pages are two-column,
4,760 lines cross the gutter, and the corpus contained things like

    "decreased GFR, possibly because BMI in isolation is a 'best practice' suggestion."

which is fluent, grammatical, page-numbered, and appears nowhere in the guideline. #19
blesses it, because the quote genuinely is in the chunk — the chunk is what is wrong, and
#19 checks quotes against chunks.

**The gutter is found, not assumed.** `page.width / 2` is a guess, and a guess standing in
for a measurement is exactly what produced this bug: the previous detector assumed the
midpoint and counted words *starting* near it — but on a two-column page the right column
starts at the midpoint, so it counted the whole right column as gutter and reported
single-column for all 163 pages. The heuristic was inverted and returned precisely the
wrong answer, which is why `tests/test_columns.py` leads with the inversion test.

So: project every character onto the x axis, and look for a vertical band that nothing
occupies. That is what a gutter *is*. It needs no assumption about where the page's centre
falls, and it finds an off-centre one.

**Refusing is the safe answer and this refuses often.** Cropping a single-column page at
an imagined gutter destroys every line on it. Every threshold below is set so that
uncertainty resolves to "one column, leave it alone": too little text, too narrow a band,
one side empty, a band too near the margins. Under-calling loses the fix on a page;
over-calling invents damage on a page that was fine.
"""

from __future__ import annotations

from dataclasses import dataclass

# A gutter is a structural band. Narrower than this is word spacing, an indent, a tab
# stop — punctuation, not layout. Measured against KDIGO, whose real gutters run ~20-30pt.
MIN_GUTTER_WIDTH = 12.0

# Only the middle of the page can hold a gutter. The blank margins at the edges are also
# empty bands, and a "gutter" found at x=20 would crop the page into a sliver and the rest.
SEARCH_BAND = (0.30, 0.70)

# Both columns must be populated. Text pushed left with an empty right half leaves a band
# that looks exactly like a gutter and is not one: there is no second column to read.
MIN_SIDE_SHARE = 0.15

# Below this there is not enough evidence to conclude anything — a title page, a divider.
# Concluding anyway is the mistake this whole module is a response to.
MIN_CHARS = 120

# Resolution of the projection. Finer than a character is pointless; coarser would miss a
# real gutter between tight columns.
BUCKET = 2.0

# --- Borderless-table veto (#44) ---------------------------------------------------------
#
# find_gutter already refuses to split when a *detected* table spans the gutter (see the
# `table_bboxes` loop below). But pdfplumber's find_tables is line-based and cannot see a
# borderless table — one ruled by whitespace alone, like KDIGO's GFR-category classification
# table. With no bbox to veto on, the gap between the table's own columns reads as a page
# gutter, the page is cropped there, and every row is torn in half: "G1" lands in one chunk,
# "Normal or high" in another. A correct model quote of the reassembled table then fails #19
# and the clinician gets no answer. This is the regression #42 introduced.
#
# The discriminator is geometric, not line-based: in flowing prose almost every line runs to
# the column margin (it wraps there), so the fraction of "full-width" lines is high; in a
# table the cells are short labels and numbers, so that fraction is low. Measured on KDIGO's
# two-column bands: the clearly-tabular ones cluster at or below 19% full-width lines (the
# GFR table at 17%, a forest plot at 0%, a comparison table at 10%, study-data tables at
# 14-19%), while flowing prose floors at 57% — and that floor is *indented* prose (numbered
# recommendations with hanging indents, p117), which is the hardest prose case because its
# short lines most resemble a table. TABLE_FILL_MAX sits at 0.30: above the tabular cluster
# (19%) with margin, and 27 points below the prose floor (57%).
#
# Tables whose fill lands in the 30-57% overlap (KDIGO's abbreviation and albuminuria-
# category tables, ~38-40%) are deliberately NOT caught. Catching them means a threshold
# close enough to the prose floor to risk welding indented prose, and a false positive here
# is a *safety* regression: welded prose passes #19 and reaches the clinician, whereas a
# missed table stays in today's safe state (no answer). So this errs hard toward missing
# tables. Recall is bought later, with a second two-column publisher to measure precision
# against; this corpus has exactly one (ESC/GOLD/NICE are single-column), so the threshold
# is validated on n=1 and kept conservative on purpose.
TABLE_FILL_MAX = 0.30

# A line "reaches the margin" if it spans at least this fraction of its column's width. 0.60
# is loose enough that a paragraph's short last line still counts short lines against prose
# only mildly, and tight enough that a table cell never reaches it.
TABLE_FULL_LINE = 0.60

# The veto only fires on *short* bands. The one demonstrated prose false-positive mode —
# indented recommendation text — occurs on full-page bands (p117 is 660pt, 87 lines), so a
# height cap excludes it by construction, a second guard behind the fill threshold. It also
# means a whole-page call to find_gutter (band = the entire page) is never table-vetoed, so
# single-column detection is unaffected. Set above every tabular band measured on KDIGO (the
# tallest caught is a ~190pt study table) and well below a prose page. A genuinely tall
# borderless table is left in the safe no-answer state rather than risk a tall-prose weld.
MAX_TABLE_BAND_HEIGHT = 250.0


@dataclass(frozen=True)
class Gutter:
    """A vertical band of the page that no character occupies."""

    x0: float
    x1: float

    @property
    def x(self) -> float:
        """The middle of the band — where a crop should cut."""
        return (self.x0 + self.x1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0


def _looks_like_borderless_table(chars: list[dict], x0: float, x1: float) -> bool:
    """Whether the two columns either side of a candidate gutter are table cells, not prose.

    Prose lines wrap to the column margin, so most are "full width"; table cells are short
    labels and numbers, so few are. This measures, per side, the fraction of lines that
    reach the margin, and calls it a table when the *lesser* side is mostly short lines and
    the band is short enough that no full-page prose block qualifies. The lesser side, not
    both: a real table can pair a wide description column with a narrow code column (KDIGO's
    GFR table fills 100% on the left, 17% on the right), so requiring both to be short would
    miss it — while measured prose keeps *both* sides above the threshold (its floor is 57%,
    on indented recommendation text), so taking the minimum does not let prose through. See
    TABLE_FILL_MAX.

    A char's line is its row bucket (``top // ROW_HEIGHT``); a line's width is the extent of
    its chars on that side. A side's column width is measured from PAGE geometry — the gutter
    to the text block's edge — NOT from the side's own text extent. That distinction is the
    whole measurement: using the text extent makes a line fill its own span by definition
    (a cell is always 100% of the widest cell), which hides the very shortness that marks a
    table. Measuring against the gutter-to-margin width counts a cell's surrounding blank as
    the column, so short cells read short. This is a char-level stand-in for
    extract_text_lines, which needs a page object find_gutter does not have — close enough
    for a threshold with a 27-point margin.
    """
    if not chars:
        return False
    top = min(c["top"] for c in chars)
    bottom = max(c["bottom"] for c in chars)
    if bottom - top > MAX_TABLE_BAND_HEIGHT:
        return False

    gx = (x0 + x1) / 2
    block_left = min(c["x0"] for c in chars)
    block_right = max(c["x1"] for c in chars)

    fractions: list[float] = []
    for side_chars, col_lo, col_hi in (
        ([c for c in chars if c["x1"] <= x0], block_left, gx),  # left column
        ([c for c in chars if c["x0"] >= x1], gx, block_right),  # right column
    ):
        if not side_chars:
            return False
        span = col_hi - col_lo
        if span < 1:
            return False
        lines: dict[int, tuple[float, float]] = {}
        for c in side_chars:
            row = int(c["top"] // ROW_HEIGHT)
            if row in lines:
                lo, hi = lines[row]
                lines[row] = (min(lo, c["x0"]), max(hi, c["x1"]))
            else:
                lines[row] = (c["x0"], c["x1"])
        full = sum(1 for lo, hi in lines.values() if (hi - lo) >= TABLE_FULL_LINE * span)
        fractions.append(full / len(lines))

    # The lesser side decides: a table needs only one clearly-short column, prose keeps both
    # sides reaching the margin.
    return min(fractions) < TABLE_FILL_MAX


def find_gutter(
    chars: list[dict],
    *,
    page_width: float,
    table_bboxes: list[tuple[float, float, float, float]] | None = None,
) -> Gutter | None:
    """The empty vertical band separating two columns, or None if there is not one.

    None means "read this page as one column", and it is the answer whenever the evidence
    is thin. That asymmetry is deliberate: a missed gutter leaves a page extracted the way
    every page is extracted today, while a false one bisects a page that was fine.

    `table_bboxes` is not optional in spirit. A table's column boundary is an empty
    vertical band the full height of the table, which is indistinguishable from a page
    gutter by geometry alone — and cropping there tears every row in half. Measured on
    KDIGO: the three *widest* bands found in the whole document were all abbreviation
    tables (p11: "BUN | Blood urea nitrogen | HBV | Hepatitis B virus"), while the real
    gutters were the narrow ones. Width does not distinguish them. Only the table does.
    """
    if len(chars) < MIN_CHARS:
        return None

    # Which slices of the x axis have any ink in them.
    occupied: set[int] = set()
    for char in chars:
        start = int(char["x0"] // BUCKET)
        end = int(char["x1"] // BUCKET)
        occupied.update(range(start, end + 1))

    lo = int(page_width * SEARCH_BAND[0] // BUCKET)
    hi = int(page_width * SEARCH_BAND[1] // BUCKET)

    # The widest empty run inside the search band. Widest rather than first: a page can
    # have several small gaps and one real gutter, and the real one is the big one.
    best: tuple[int, int] | None = None
    run_start: int | None = None
    for bucket in range(lo, hi + 1):
        if bucket in occupied:
            if run_start is not None:
                if best is None or (bucket - run_start) > (best[1] - best[0]):
                    best = (run_start, bucket)
                run_start = None
        elif run_start is None:
            run_start = bucket
    if run_start is not None and (best is None or (hi + 1 - run_start) > (best[1] - best[0])):
        best = (run_start, hi + 1)

    if best is None:
        return None

    x0, x1 = best[0] * BUCKET, best[1] * BUCKET
    if x1 - x0 < MIN_GUTTER_WIDTH:
        return None

    # A table spanning the band means the band is a table's own column boundary. Cropping
    # there splits every row into two fragments in two places, and the cells stop meaning
    # anything. Better to read the page across — the flowed text of a table is already
    # row-wise, which is the shape it should have.
    for bbox in table_bboxes or []:
        if bbox[0] < x0 and bbox[2] > x1:
            return None

    # Both sides populated, or it is one column with whitespace beside it.
    left = sum(1 for c in chars if c["x1"] <= x0)
    right = sum(1 for c in chars if c["x0"] >= x1)
    total = len(chars)
    if left < total * MIN_SIDE_SHARE or right < total * MIN_SIDE_SHARE:
        return None

    # A borderless table's inter-column gap looks exactly like a page gutter (find_tables
    # cannot see it to veto it), but cropping there tears every row in half. Same remedy as
    # the detected-table veto above: refuse the gutter, read the band across — a table's
    # flowed text is already row-wise, which is the shape it should have. (#44)
    if _looks_like_borderless_table(chars, x0, x1):
        return None

    return Gutter(x0=x0, x1=x1)


def crosses(gutter: Gutter, bbox: tuple[float, float, float, float]) -> bool:
    """Whether something spans the gutter — a full-width heading, or a table row.

    Cropping a page that contains one of these cuts it in half: a table row becomes two
    fragments in two different places, and its cells stop meaning anything. The caller
    decides what to do; this only answers the question.
    """
    x0, _, x1, _ = bbox
    return x0 < gutter.x0 and x1 > gutter.x1


# Rows this tall. Fine enough to find the boundary between a figure and the text under
# it; coarse enough that a superscript does not split a band.
ROW_HEIGHT = 4.0

# Bands shorter than this are not layout: a two-line gap between a heading and a caption
# is not a column region, and cropping one splits a heading in half.
#
# In *points*, and named so. The first version was MIN_BAND_ROWS = 6, which read as "six
# lines of text" and counted six 4pt slices — about two lines. A mutation run deleted it
# and nothing failed, because the guard was doing roughly nothing: the name promised one
# thing and the unit delivered another. 48pt is three lines of 16pt body text.
MIN_BAND_HEIGHT = 48.0

# Rows this far apart belong to different bands even if they agree — the gap between a
# figure and the body text is itself a boundary.
#
# In rows (× ROW_HEIGHT = pt). 3 was calibrated on KDIGO and quietly wrong: at 12pt it
# sat in the *middle* of KDIGO's own body line-pitch (9-14pt), so it fragmented a
# two-column page into per-line bands, each then rejected by MIN_BAND_HEIGHT — and the
# whole page read as one column and welded. It was invisible because KDIGO's two-column
# pages are short bands under figures; a document that is two-column *throughout* with
# normal 14pt leading exposed it. Measured line-gap distribution on KDIGO two-column
# pages: body clusters at 9-14pt, real section/figure breaks jump to 25pt+. 5 rows (20pt)
# sits cleanly between them — it bridges a normal line pitch and still breaks at a figure.
MAX_ROW_GAP = 5


@dataclass(frozen=True)
class Region:
    """A horizontal band of a page, and the gutter inside it if there is one."""

    top: float
    bottom: float
    gutter: Gutter | None

    @property
    def is_two_column(self) -> bool:
        return self.gutter is not None


def find_regions(
    chars: list[dict],
    *,
    page_width: float,
    page_height: float,
    table_bboxes: list[tuple[float, float, float, float]] | None = None,
) -> list[Region]:
    """Split a page into bands and find the gutter in each.

    **Why bands and not a page.** Projecting the whole page height means one full-width
    figure fills the gutter and the entire page reads as single-column — which is how six
    clinically-loaded welds survived the first fix. Measured on KDIGO p60: a figure
    occupies rows 220-276, and the 103 rows of two-column body text below it were being
    read across because of it.

    **Why not just a better threshold.** The obvious repair is to ask "is the centre clear
    in most rows" and pick a number. Measured across all 163 pages, that number does not
    exist: the distribution runs continuously from 27% to 100% with 16 pages sitting in
    the 55-75% band where any threshold would have to go. Those pages are not ambiguous,
    they are *mixed* — a figure above, columns below. The question "is this page
    two-column" has no answer. "Is this band two-column" does.

    Each band is then handed to `find_gutter`, so every guard that protects a page
    protects a band: too little text, too narrow a gap, one empty side, a table across
    the middle. A band of six short lines with a coincidental gap in the middle is
    refused for the same reasons a page would be.
    """
    if not chars:
        return [Region(top=0.0, bottom=page_height, gutter=None)]

    mid = page_width / 2
    occupied_rows: dict[int, bool] = {}
    for char in chars:
        row = int(char["top"] // ROW_HEIGHT)
        occupied_rows.setdefault(row, False)
        if char["x0"] <= mid <= char["x1"]:
            occupied_rows[row] = True

    # Contiguous runs of rows that agree about the centre.
    bands: list[tuple[int, int, bool]] = []
    for row in sorted(occupied_rows):
        crosses_centre = occupied_rows[row]
        if bands and bands[-1][2] == crosses_centre and row - bands[-1][1] <= MAX_ROW_GAP:
            bands[-1] = (bands[-1][0], row, crosses_centre)
        else:
            bands.append((row, row, crosses_centre))

    regions: list[Region] = []
    for lo, hi, crosses_centre in bands:
        top = lo * ROW_HEIGHT
        bottom = (hi + 1) * ROW_HEIGHT
        # `crosses_centre` here is an optimisation, not a guard: find_gutter refuses a
        # band whose centre is occupied anyway, because there is no empty band to find.
        # Skipping the call is free; relying on it as protection would not be, so the
        # height check below is the one that has to hold.
        if crosses_centre or (bottom - top) < MIN_BAND_HEIGHT:
            regions.append(Region(top=top, bottom=bottom, gutter=None))
            continue

        band_chars = [c for c in chars if top <= c["top"] < bottom]
        band_tables = [
            b for b in (table_bboxes or []) if not (b[3] < top or b[1] > bottom)
        ]
        regions.append(
            Region(
                top=top,
                bottom=bottom,
                gutter=find_gutter(
                    band_chars, page_width=page_width, table_bboxes=band_tables
                ),
            )
        )

    return regions
