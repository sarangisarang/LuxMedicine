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


# --- a borderless table's boundary is not a page gutter either (#44) ------------------


def two_column_table_chars(width: float = 600):
    """A borderless table, not prose — shaped like KDIGO's GFR-category table.

    find_tables cannot see a table ruled by whitespace alone, so no bbox exists to veto its
    inter-column gap — and that gap reads as a page gutter, cropping every row in half. The
    GFR table pairs a *full-width* left column (category descriptions that reach the margin,
    17% short — i.e. 100% full) with a *short* right column (stage codes and numbers). That
    asymmetry is deliberate here: it is why the veto takes the lesser side, not both, and a
    fixture with only one short column is what makes a min()->max() mutation fail.
    """
    chars = []
    for row in range(6):
        y = row * 12.0
        for x in range(40, 288, 2):  # left: a full-width description cell, like prose
            chars.append(FakeChar(x, x + 1, y, y + 10))
        right_end = 540 if row == 0 else 360  # right: a wide header, then short cells
        for x in range(312, right_end, 2):
            chars.append(FakeChar(x, x + 1, y, y + 10))
    return chars


def two_column_ragged_prose_chars(width: float = 600):
    """Two prose columns where about a third of lines end short, as real paragraphs do.

    An idealized prose fixture fills every line to the margin (100%), and 100% is not below
    a threshold of 1.0 — so it cannot catch TABLE_FILL_MAX set too high, which would veto
    real prose and weld it. This fills ~67%, above the 30% table line but well below 100%,
    and guards that upper bound: KDIGO's prose floors at 57%, and none of it may be vetoed.
    """
    chars = []
    for row in range(9):
        y = row * 12.0
        short = row % 3 == 2  # every third line ends short, like a paragraph's last line
        for start, margin in ((40, 288), (312, 560)):
            end = start + 80 if short else margin
            for x in range(start, end, 2):
                chars.append(FakeChar(x, x + 1, y, y + 10))
    return chars


def test_a_borderless_table_is_not_split_and_prose_still_is():
    """Both directions in one assertion, for the same reason the inversion test is: a veto
    that fires on everything would stop the welds by refusing every crop, regressing #42 in
    silence; one that fires on nothing leaves the table scrambled. The table must lose its
    gutter and the prose must keep it."""
    assert find_gutter(two_column_table_chars(), page_width=600) is None, (
        "a borderless table's column boundary was cropped — every row torn in half (#44)"
    )
    assert find_gutter(two_column_chars(), page_width=600) is not None, (
        "the veto refused a genuine prose gutter — this welds the columns, regressing #42"
    )


def test_the_table_signal_separates_cells_from_prose():
    """The discriminator in isolation, so a mutation to the threshold or the min() is caught
    even if find_gutter's other guards happened to mask it. A table cell fills little of its
    column; a prose line reaches the margin. Measured on KDIGO, tabular bands sit at or below
    19% full-width lines and prose floors at 57%, so the check lives at 30%.

    The table's left column is full-width (like prose) and only its right column is short,
    so min() calls it a table and max() would not — the assertion that catches a min()->max()
    mutation. Prose is full-width on both sides, so neither reduction lets it through.
    """
    from app.services.columns import _looks_like_borderless_table

    # A gutter split placed safely inside each fixture's empty band (~288..312).
    assert _looks_like_borderless_table(two_column_table_chars(), 295, 310) is True
    assert _looks_like_borderless_table(two_column_chars(), 295, 310) is False
    # and end to end, the whole find_gutter refuses the table and keeps the prose.
    assert find_gutter(two_column_table_chars(), page_width=600, table_bboxes=[]) is None


def test_ragged_prose_is_not_vetoed_by_a_too_high_threshold():
    """The upper bound of TABLE_FILL_MAX. Prose whose lines end short a third of the time
    (~67% full) is unmistakably prose, but an idealized 100%-full fixture cannot prove a
    threshold set near 1.0 would spare it. This can: at 0.30 it stays two-column, and a
    threshold high enough to catch it would be welding real prose (KDIGO floors at 57%)."""
    from app.services.columns import _looks_like_borderless_table

    assert _looks_like_borderless_table(two_column_ragged_prose_chars(), 295, 310) is False
    assert find_gutter(two_column_ragged_prose_chars(), page_width=600, table_bboxes=[]) is not None


def test_a_tall_two_column_block_is_never_called_a_table():
    """The height cap, reached on purpose. Indented prose — numbered recommendations with
    hanging indents — has short lines like a table's, and on KDIGO it floors the fill signal
    at 57% only because it runs a full page. A short band of the same text could dip lower,
    so the veto refuses to fire on anything taller than a table's band. This protects the one
    demonstrated prose false-positive mode (KDIGO p117, a 660pt recommendation block) by
    construction, behind the fill threshold rather than relying on it alone."""
    from app.services.columns import MAX_TABLE_BAND_HEIGHT, _looks_like_borderless_table

    # The table fixture, but stretched past the cap: same short-cell shape, taller than any
    # table band. It must not be vetoed however short its lines are.
    tall = []
    step = (MAX_TABLE_BAND_HEIGHT + 60) / 6
    for row in range(6):
        y = row * step
        for x in range(40, 70, 2):
            tall.append(FakeChar(x, x + 1, y, y + 10))
        right_end = 540 if row == 0 else 360
        for x in range(312, right_end, 2):
            tall.append(FakeChar(x, x + 1, y, y + 10))

    assert (tall[-1]["bottom"] - tall[0]["top"]) > MAX_TABLE_BAND_HEIGHT
    assert _looks_like_borderless_table(tall, 180, 312) is False


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_the_gfr_classification_table_reads_across_not_down():
    """The table this fix exists for. KDIGO's GFR-category table is the most-cited table in
    the guideline — 'how is CKD classified by GFR' — and #42 read it column-wise, detaching
    'G1' from 'Normal or high' so a correct model quote of it failed #19 and the clinician
    got no answer. Read across, each stage code sits on the same line as its category term.
    """
    from app.services.extraction import extract_pdf

    doc = extract_pdf(KDIGO)
    page = next(p for p in doc.pages if p.number == 18)
    row = next(
        (ln for ln in page.text.split("\n") if "G1" in ln and "Normal or high" in ln), None
    )

    assert row is not None, (
        "the GFR table is scrambled: 'G1' and 'Normal or high' landed on different lines, "
        "so the table was read down its columns instead of across its rows (#44)"
    )


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


# --- bands, because a page is not homogeneous ---------------------------------------


def test_a_figure_across_the_middle_no_longer_hides_the_columns():
    """The bug that survived the first fix.

    Projecting the whole page height means one full-width element fills the gutter and
    the page reads as single-column. Measured on KDIGO p60: a figure at rows 220-276 was
    hiding 103 rows of two-column body text below it, and six clinically-loaded welds
    lived on pages exactly like it.
    """
    from app.services.columns import find_regions

    chars = two_column_chars()  # rows 0..39, gutter at 300
    # A caption straight across the middle, in the band above.
    chars += [FakeChar(x, x + 5, 480, 490) for x in range(40, 560, 6)]

    assert find_gutter(chars, page_width=600) is None, (
        "whole-page projection is blind to this — that is the bug"
    )

    regions = find_regions(chars, page_width=600, page_height=600)
    two_col = [r for r in regions if r.is_two_column]

    assert two_col, "the columns above the figure must still be found"
    assert any(not r.is_two_column for r in regions), "the figure's band is not two-column"


def test_a_band_of_short_lines_is_not_two_columns():
    """A clear centre in a band does not prove two columns: six short lines have a clear
    centre too. Every guard that protects a page has to protect a band, or segmentation
    just makes the detector wrong more precisely."""
    from app.services.columns import find_regions

    chars = [FakeChar(40 + i * 6, 45 + i * 6, row * 12.0, row * 12.0 + 10)
             for row in range(8) for i in range(10)]  # short lines, left side only

    regions = find_regions(chars, page_width=600, page_height=600)

    assert not any(r.is_two_column for r in regions)


def test_regions_stay_in_page_order():
    """A figure between two column regions must stay between them. Reordering bands would
    move a caption to the top of the page and attach it to the wrong thing — the same
    class of error as reading the columns backwards."""
    from app.services.columns import find_regions

    chars = two_column_chars()
    chars += [FakeChar(x, x + 5, 480, 490) for x in range(40, 560, 6)]

    regions = find_regions(chars, page_width=600, page_height=600)
    tops = [r.top for r in regions]

    assert tops == sorted(tops)


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_no_surviving_weld_carries_clinical_content():
    """The number that decides whether this is finished.

    205 welded lines originally; 53 after cropping whole pages; 15 after segmenting them
    into bands. What matters is not the count but what is in them: 6 of the 53 welded a
    dose, a recommendation, or clinical vocabulary — 'subgroup with eGFR 45-59ml/min/1.73m2,
    the com- If cystatin C testing is desired'. None of the 15 do. The remainder are
    author lists and references: untidy, and unable to mislead anyone about a dose.
    """
    import re

    from app.services.extraction import extract_pdf

    weld = re.compile(r"[a-z]{3,}-\s+[a-z]", re.I)
    clinical = re.compile(
        r"\b\d+(\.\d+)?\s?(mg|mcg|g|ml|mmol|units?)\b"
        r"|\bwe (recommend|suggest)\b"
        r"|\b(GFR|eGFR|creatinine|albuminuria|dialysis)\b",
        re.I,
    )

    doc = extract_pdf(KDIGO)
    dangerous = [
        line.text
        for line in doc.lines
        if weld.search(line.text) and clinical.search(line.text)
    ]

    assert not dangerous, (
        f"{len(dangerous)} welded line(s) carry clinical content, e.g. {dangerous[0][:90]!r}"
    )


def test_a_short_dense_band_is_refused_by_height_not_by_luck():
    """MIN_BAND_HEIGHT, reached on purpose.

    The first version was MIN_BAND_ROWS = 6, which read as "six lines" and counted six
    4pt slices — about two lines of text. A mutation deleted it and nothing failed. The
    input below is exactly what it is supposed to stop: a short band, dense enough to
    pass MIN_CHARS, with both sides populated and a real gap between them. find_gutter
    alone accepts it; only the height check refuses.
    """
    from app.services.columns import find_gutter, find_regions

    chars = []
    for row in range(3):
        y = row * 12.0
        for x in range(40, 288, 2):
            chars.append(FakeChar(x, x + 1, y, y + 10))
        for x in range(312, 560, 2):
            chars.append(FakeChar(x, x + 1, y, y + 10))

    assert find_gutter(chars, page_width=600) is not None, "the guard must be reachable"
    assert not any(
        r.is_two_column for r in find_regions(chars, page_width=600, page_height=600)
    ), "a 36pt band is a heading, not a column region"


# --- a full two-column page, through the real extractor (#42 generalisation) ---------


def _build_two_column_pdf(path, *, leading: float, pages: int = 3):
    """A PDF that is two-column top to bottom, with a known left and right topic.

    reportlab is a dev dependency (it builds the extraction fixtures). Left column is about
    sodium, right about potassium — nothing from one belongs in the other, so a welded line
    is unmistakable. `leading` is the parameter that exposed the bug: KDIGO's bands are
    tight, and MAX_ROW_GAP was calibrated below a normal line pitch.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    left = (
        "The left column discusses sodium. Sodium restriction below two grams per day is "
        "recommended for patients with resistant hypertension. Adherence remains the "
        "principal difficulty and dietary counselling is essential for any benefit."
    ).split()
    right = (
        "The right column discusses potassium. Potassium supplementation is contraindicated "
        "in reduced kidney function because of hyperkalaemia. Serum levels must be monitored "
        "whenever a renin inhibitor is introduced to prevent dangerous accumulation."
    ).split()

    def column(c, words, x, width, top=800):
        y, line = top, ""
        for w in words:
            if c.stringWidth(f"{line} {w}".strip(), "Helvetica", 10) > width:
                c.drawString(x, y, line)
                y -= leading
                line = w
            else:
                line = f"{line} {w}".strip()
        if line:
            c.drawString(x, y, line)

    c = canvas.Canvas(str(path), pagesize=A4)
    for _ in range(pages):
        c.setFont("Helvetica", 10)
        column(c, left, 50, 220)
        column(c, right, 310, 220)
        c.showPage()
    c.save()


def test_a_full_two_column_page_reads_down_then_across(tmp_path):
    """The caveat #42 left open: find_regions was proven on KDIGO's two-column *bands*,
    never on a page that is two-column *throughout*. Built here at 14pt leading — normal
    for 10pt text — which is what exposed MAX_ROW_GAP sitting below a real line pitch and
    fragmenting every such page into per-line bands that MIN_BAND_HEIGHT then discarded.
    The page then read as one column and welded.
    """
    from app.services.extraction import extract_pdf

    pdf = tmp_path / "twocol.pdf"
    _build_two_column_pdf(pdf, leading=14.0)

    doc = extract_pdf(pdf)
    lines = [ln for ln in doc.pages[0].text.split("\n") if ln.strip()]

    # No line may contain both topics: that is a weld across the gutter.
    for line in lines:
        low = line.lower()
        assert not ("sodium" in low and "potassium" in low), f"welded across the gutter: {line!r}"

    # The whole left column precedes the whole right — read down, then across.
    joined = " ".join(lines).lower()
    assert joined.find("sodium") < joined.find("potassium")
    assert "the right column discusses potassium" in joined


def test_the_detector_survives_loose_leading(tmp_path):
    """The specific regression. At 16pt leading a two-column page must still be detected;
    MAX_ROW_GAP must bridge a normal line pitch, not cut through it."""
    import pdfplumber

    from app.services.columns import find_regions

    pdf = tmp_path / "loose.pdf"
    _build_two_column_pdf(pdf, leading=16.0)

    with pdfplumber.open(pdf) as opened:
        page = opened.pages[0]
        regions = find_regions(page.chars, page_width=page.width, page_height=page.height)

    assert any(r.is_two_column for r in regions), (
        "a two-column page at 16pt leading read as single-column — MAX_ROW_GAP is below "
        "the line pitch again"
    )
