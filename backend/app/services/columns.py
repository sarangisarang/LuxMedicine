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

    return Gutter(x0=x0, x1=x1)


def crosses(gutter: Gutter, bbox: tuple[float, float, float, float]) -> bool:
    """Whether something spans the gutter — a full-width heading, or a table row.

    Cropping a page that contains one of these cuts it in half: a table row becomes two
    fragments in two different places, and its cells stop meaning anything. The caller
    decides what to do; this only answers the question.
    """
    x0, _, x1, _ = bbox
    return x0 < gutter.x0 and x1 > gutter.x1
