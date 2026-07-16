"""Rotated landscape tables, held out rather than read backwards (#43 follow-up).

10 of KDIGO's 163 pages are landscape tables printed with the text turned 90°. pdfplumber
reads rotated glyphs in reversed visual order, so 'SCr calibration and assay' arrives as
'yassadnanoitarbilacrCS'. A reversed dosing table quoted verbatim passes #19 exactly as a
broken formula does — the chunk is self-consistent, and #19 checks quotes against chunks.

**Detected from the glyphs, not from a proxy.** Three signals were measured against the
ten known-bad pages and the 153 known-good ones:

  page.rotation != 0   flagged 0   — empty; the sheet is upright, only its content turned
  width > height       flagged 0   — these pages are portrait; the table was rotated
  upright=False share   flagged 10  — the thing that is actually wrong, and nothing else

The first two are proxies for rotation that can be absent when it is present. The third is
rotation. That is the whole lesson of the extractor bugs: measure the thing, not a thing
near it.
"""

import pathlib

import pytest

KDIGO = pathlib.Path(__file__).resolve().parents[1] / "storage" / "kdigo_2012_ckd.pdf"

# Ground truth, established by reading the reversed strings back to front.
KNOWN_ROTATED = {54, 55, 57, 58, 61, 62, 65, 67, 80, 135}


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_the_rotated_pages_are_held_out():
    from app.services.extraction import extract_pdf

    doc = extract_pdf(KDIGO)

    assert set(doc.rotated_pages) == KNOWN_ROTATED


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_no_reversed_text_reaches_the_corpus():
    """The point. A held-out page contributes no lines, so the backwards strings that
    were there — 'yassadnanoitarbilacrCS' and its kind — are simply gone from doc.text."""
    from app.services.extraction import extract_pdf

    doc = extract_pdf(KDIGO)

    # These are the reversed forms of real headings on the rotated pages.
    for reversed_fragment in ("yassadnanoitarbilac", "lanretnidnatnempoleveD"):
        assert reversed_fragment not in doc.text, (
            f"{reversed_fragment!r} reached the corpus — a rotated page was read backwards"
        )


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_held_out_pages_are_reported_not_dropped():
    """Silence is the failure this project keeps finding. A rotated page removed without a
    record reads to the clinician as 'the guideline does not cover that', which is the
    confusion #20 exists to prevent. The pages must appear on damaged_pages."""
    from app.services.extraction import extract_pdf

    doc = extract_pdf(KDIGO)

    for page in KNOWN_ROTATED:
        assert page in doc.damaged_pages


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_page_numbering_stays_aligned():
    """A held-out page keeps its slot in the ledger, or every citation after it points one
    page too early — a quieter version of the corruption being fixed."""
    from app.services.extraction import extract_pdf

    doc = extract_pdf(KDIGO)

    # Every page 1..163 is present in the ledger, held-out ones included.
    numbers = [p.number for p in doc.pages]
    assert numbers == list(range(1, 164))
    for page in KNOWN_ROTATED:
        held = next(p for p in doc.pages if p.number == page)
        assert held.text == "", "a held-out page must carry no text"


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_the_proxies_would_have_missed_all_ten():
    """Recorded so nobody 'simplifies' the detector to page.rotation or width>height
    later. Both are empty on exactly the pages that are wrong."""
    import pdfplumber

    with pdfplumber.open(KDIGO) as pdf:
        by_rotation = {p.page_number for p in pdf.pages if p.rotation}
        by_landscape = {p.page_number for p in pdf.pages if p.width > p.height}

    assert not (by_rotation & KNOWN_ROTATED), "page.rotation would have caught some — re-check"
    assert not (by_landscape & KNOWN_ROTATED), "width>height would have caught some — re-check"


def test_an_upright_page_is_not_flagged():
    from app.services.extraction import _is_rotated

    class Page:
        chars = [{"text": "x", "upright": True} for _ in range(200)]

    assert not _is_rotated(Page())


def test_a_page_of_sideways_characters_is_flagged():
    from app.services.extraction import _is_rotated

    class Page:
        chars = [{"text": "x", "upright": False} for _ in range(200)]

    assert _is_rotated(Page())


def test_a_near_empty_page_is_not_flagged_by_a_stray_rotation():
    """A rotated watermark or a single turned label on an otherwise upright page is not a
    rotated table. The share is only meaningful over enough characters to divide by."""
    from app.services.extraction import _is_rotated

    class Page:
        chars = [{"text": "x", "upright": False} for _ in range(5)]

    assert not _is_rotated(Page())
