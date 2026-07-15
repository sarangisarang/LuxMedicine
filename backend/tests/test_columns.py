"""Finding the gutter (#42).

**This file exists because the last detector was inverted and returned exactly the wrong
answer.** It counted words *starting* near the centre — and on a two-column page the right
column starts at the centre, so it counted the entire right column as gutter and called
every page single-column. That is how 4,760 welded lines reached the corpus while a
measurement said the risk was untestable.

So the inversion test is first, and it is the point of the file. A detector that says
"single-column" for everything passes any test that only checks two-column pages; a
detector that says "two-column" for everything passes any test that only checks
two-column pages *and* would bisect every single-column page in the corpus. Both
directions have to fail loudly.

The geometry is measured, not assumed. The gutter is *found* — projecting characters onto
the x axis and looking for a vertical band nothing occupies — rather than presumed to sit
at `width / 2`. Assuming the midpoint is the same class of mistake that caused this bug:
a plausible guess standing in for a measurement.
"""

import pathlib

import pytest

from app.services.columns import Gutter, find_gutter

KDIGO = pathlib.Path(__file__).resolve().parents[1] / "storage" / "kdigo_2012_ckd.pdf"


class FakeChar(dict):
    """The two keys find_gutter reads. A dict because pdfplumber's chars are dicts."""

    def __init__(self, x0: float, x1: float, top: float = 0.0, bottom: float = 10.0):
        super().__init__(x0=x0, x1=x1, top=top, bottom=bottom)


def two_column_chars(width: float = 600, gutter_at: float = 300, gap: float = 24):
    """Text in two bands with an empty strip between them."""
    chars = []
    for row in range(40):
        y = row * 12.0
        for x in range(40, int(gutter_at - gap / 2), 6):
            chars.append(FakeChar(x, x + 5, y, y + 10))
        for x in range(int(gutter_at + gap / 2), int(width) - 40, 6):
            chars.append(FakeChar(x, x + 5, y, y + 10))
    return chars


def single_column_chars(width: float = 600):
    """Text flowing straight across the middle. No band is ever empty."""
    chars = []
    for row in range(40):
        y = row * 12.0
        for x in range(40, int(width) - 40, 6):
            chars.append(FakeChar(x, x + 5, y, y + 10))
    return chars


# --- the inversion test, first ------------------------------------------------------


def test_a_single_column_page_has_no_gutter():
    """The test the last detector would have failed, and the reason this file leads with
    it. Cropping a single-column page at its midpoint destroys every line on it — a
    detector that is wrong in this direction is worse than no detector at all."""
    assert find_gutter(single_column_chars(), page_width=600) is None


def test_a_two_column_page_has_one():
    gutter = find_gutter(two_column_chars(), page_width=600)

    assert gutter is not None
    assert gutter.x == pytest.approx(300, abs=15)


def test_the_detector_is_not_inverted():
    """Belt and braces, stated as one assertion so nobody can satisfy half of it.

    The previous heuristic answered "single-column" for both inputs. A future one that
    answers "two-column" for both would pass every test above that checks a two-column
    page. Both have to be wrong before this passes.
    """
    assert find_gutter(single_column_chars(), page_width=600) is None
    assert find_gutter(two_column_chars(), page_width=600) is not None


# --- the gutter is found, not assumed -----------------------------------------------


@pytest.mark.parametrize("gutter_at", [240, 300, 360])
def test_an_off_centre_gutter_is_still_found(gutter_at):
    """`width / 2` is a guess. Guessing where the gutter is, on a page whose whole
    problem is that we guessed wrong about its layout, would be the same mistake wearing
    a different hat."""
    gutter = find_gutter(two_column_chars(gutter_at=gutter_at), page_width=600)

    assert gutter is not None
    assert gutter.x == pytest.approx(gutter_at, abs=15)


def test_a_margin_is_not_a_gutter():
    """The whitespace at the edges is empty too, and it is not a column boundary. A
    detector that finds a 'gutter' at x=20 would crop the page into a sliver and the rest."""
    chars = single_column_chars()

    assert find_gutter(chars, page_width=600) is None


def test_a_half_empty_page_is_not_two_column():
    """Text pushed to the left with nothing on the right leaves a large empty band that
    looks exactly like a gutter. It is not: there is no second column to read."""
    chars = [c for c in single_column_chars() if c["x1"] < 280]

    assert find_gutter(chars, page_width=600) is None


def test_a_narrow_gap_is_not_a_gutter():
    """Word spacing, a tab stop, an indent. A gutter is a structural band; anything a few
    points wide is punctuation."""
    chars = two_column_chars(gap=4)

    assert find_gutter(chars, page_width=600) is None


def test_a_page_with_almost_no_text_is_refused():
    """A title page, a divider. Too little evidence to conclude anything, and concluding
    anyway is what this whole issue is about."""
    assert find_gutter([FakeChar(10, 15)], page_width=600) is None


# --- against the real document -------------------------------------------------------


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_the_real_guideline_is_sorted_correctly():
    """163 real pages. The document is mixed — body text in two columns, front matter and
    some tables across the full width — so a detector that answers the same thing for
    every page is wrong whichever thing it answers."""
    import pdfplumber

    with pdfplumber.open(KDIGO) as pdf:
        verdicts = [
            find_gutter(page.chars, page_width=page.width) is not None for page in pdf.pages
        ]

    two_col = sum(verdicts)
    assert two_col > 60, f"only {two_col}/163 two-column; the detector is under-calling"
    assert two_col < 150, f"{two_col}/163 two-column; front matter is not two-column"


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_page_118_is_two_column_and_the_gutter_is_where_the_text_is_not():
    """The page whose welded line opened #42:

    'as gadolinium is freely dialysed, most guidelines recom- determine risk-benefit...'
    """
    import pdfplumber

    with pdfplumber.open(KDIGO) as pdf:
        page = pdf.pages[117]
        gutter = find_gutter(page.chars, page_width=page.width)

    assert gutter is not None, "the page that produced the welded line reads as single-column"
    assert isinstance(gutter, Gutter)

    # Nothing may sit in the band, or it is not a gutter.
    with pdfplumber.open(KDIGO) as pdf:
        page = pdf.pages[117]
        inside = [c for c in page.chars if gutter.x0 < c["x0"] and c["x1"] < gutter.x1]
    assert not inside, f"{len(inside)} characters are inside the supposed gutter"


# --- a table's column boundary is not a page gutter ----------------------------------


def test_a_table_spanning_the_band_blocks_the_crop():
    """Geometry alone cannot tell a page gutter from a table's own column boundary: both
    are empty vertical bands. Measured on KDIGO, the three widest bands in the entire
    document were abbreviation tables — 'BUN | Blood urea nitrogen | HBV | Hepatitis B
    virus' — and the real gutters were the narrow ones. Width does not separate them.

    Cropping a table's boundary tears every row in half.
    """
    chars = two_column_chars()

    assert find_gutter(chars, page_width=600) is not None
    assert (
        find_gutter(chars, page_width=600, table_bboxes=[(40, 0, 560, 480)]) is None
    ), "a table spanning the band must veto the crop"


def test_a_table_beside_the_gutter_does_not_block_it():
    """A table living inside one column is not a reason to read the whole page across."""
    chars = two_column_chars()

    gutter = find_gutter(chars, page_width=600, table_bboxes=[(40, 0, 280, 200)])

    assert gutter is not None


# --- the outcome, on the real document ------------------------------------------------


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_the_welds_are_mostly_gone():
    """The only number that matters: does the corpus stop containing sentences nobody
    wrote?

    A weld is a word broken by a hyphen at a line end followed, on the same line, by
    unrelated prose. The continuation of "recom-" is "mend"; anything else came from the
    other column. 205 of these before, 53 after — a 74% reduction, not a cure. The
    remainder sit on pages find_gutter declines, where reading across is what happens
    today anyway.

    The bound is loose on purpose. Pinning it to exactly 53 would fail on a pdfplumber
    upgrade for reasons that have nothing to do with this fix; what must not happen is a
    silent return to 205.
    """
    import re

    from app.services.extraction import extract_pdf

    weld = re.compile(r"[a-z]{3,}-\s+[a-z]", re.I)
    doc = extract_pdf(KDIGO)
    welded = [line for line in doc.lines if weld.search(line.text)]

    assert len(welded) < 80, (
        f"{len(welded)} welded lines — it was 205 before column-aware extraction and 53 "
        "after. This many means the columns are being read across again."
    )


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_the_sentence_that_opened_the_issue_is_whole():
    """'most guidelines recom- determine risk-benefit in these patients' — the left
    column's broken word welded to the right column's sentence. Its real continuation is
    'mend dialysis in patients with a GFR <15'."""
    from app.services.extraction import extract_pdf

    doc = extract_pdf(KDIGO)
    i = doc.text.find("most guidelines recom-")

    assert i > 0, "the line is gone; the fixture or the extractor changed"
    assert doc.text[i : i + 60].split("\n")[1].startswith("mend"), (
        f"the word did not rejoin: {doc.text[i : i + 70]!r}"
    )


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_the_offset_ledger_survives_cropping():
    """The ledger is what makes a citation resolvable to a page. Reordering lines without
    reordering the offsets would leave every citation on a two-column page pointing
    somewhere it never came from — a quieter version of the bug being fixed."""
    from app.services.extraction import extract_pdf

    doc = extract_pdf(KDIGO)
    line = next(ln for ln in doc.lines if "most guidelines recom-" in ln.text)

    assert doc.pages_for_span(line.char_start, line.char_end) == (line.page, line.page)
    assert doc.text[line.char_start : line.char_end] == line.text, (
        "the ledger's span does not contain the line it describes"
    )


def test_a_sparse_two_column_page_is_refused():
    """MIN_CHARS, reached properly.

    A mutation run deleted the guard and nothing failed — the fixture had one character,
    so the *side* check refused it first and MIN_CHARS never ran. A guard no test can
    reach is a guard nobody can trust; this one needs a page with two real-looking columns
    and not much in them. A running head over a page number would do it.
    """
    chars = [c for c in two_column_chars() if c["top"] < 12]  # one row

    assert 0 < len(chars) < 120
    assert find_gutter(chars, page_width=600) is None


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_the_left_column_is_read_before_the_right():
    """Order, which the weld tests do not check.

    A mutation swapped the columns — right first, then left — and every test still passed:
    each column is internally correct, "recom-" still rejoins "mend", the ledger is still
    self-consistent. Only the *document* is backwards, and a chunk spanning the boundary
    then welds the right column's end onto the left column's start. The same bug, one
    level up, invisible to everything that checks a line.
    """
    from app.services.extraction import extract_pdf

    doc = extract_pdf(KDIGO)
    page = next(p for p in doc.pages if p.number == 118)

    # Measured from p118: this sentence ends the left column, this one is in the right.
    left_side = page.text.find("alternative appropriate test")
    right_side = page.text.find("in young children and neonates")

    assert left_side > 0 and right_side > 0, "the fixture text moved; re-measure p118"
    assert left_side < right_side, (
        "the right column is being read before the left — the page is backwards"
    )
