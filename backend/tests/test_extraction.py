"""Extraction and page-provenance tests (#8).

These run against real PDFs built by reportlab, not against hand-written fixtures.
A fixture that returns page text on demand would test the ledger while flattering the
parser — and the parser is half of what can go wrong here.
"""

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.extraction import NoTextLayerError, extract_pdf


def build_pdf(path: Path, pages: list[list[str]]) -> Path:
    """One list of lines per page."""
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for lines in pages:
        y = 800
        for line in lines:
            pdf.drawString(60, y, line)
            y -= 18
        pdf.showPage()
    pdf.save()
    return path


@pytest.fixture
def guideline(tmp_path) -> Path:
    """Three pages, each carrying the same running header — the exact condition that
    breaks a find()-based page map."""
    return build_pdf(
        tmp_path / "guideline.pdf",
        [
            ["ESC Guidelines", "Section 1 Introduction", "Heart failure affects millions."],
            ["ESC Guidelines", "Section 2 Treatment", "The target dose of enalapril is 20 mg."],
            ["ESC Guidelines", "Section 3 Follow-up", "Review the patient after four weeks."],
        ],
    )


# --- the ledger --------------------------------------------------------------------


def test_every_page_is_recorded_in_order(guideline):
    doc = extract_pdf(guideline)

    assert [p.number for p in doc.pages] == [1, 2, 3]
    assert doc.empty_pages == []


def test_recorded_offsets_actually_locate_each_page(guideline):
    """The ledger's core claim: text[char_start:char_end] IS that page's text.

    This test carries the file. Dropping the separator's width from the running cursor
    — a plausible one-token slip — was caught here and by nothing else: the
    pages_for_span tests below all stayed green, because a two-character drift is not
    enough to push their spans across a boundary in a small fixture. It would be enough
    in a real guideline, and the citation would be wrong with a real page number
    attached to it.

    So: assert the invariant directly, and never rely on the span tests to notice
    arithmetic that is merely slightly wrong.
    """
    doc = extract_pdf(guideline)

    for page in doc.pages:
        assert doc.text[page.char_start : page.char_end] == page.text


def test_a_span_resolves_to_the_page_it_came_from(guideline):
    doc = extract_pdf(guideline)

    needle = "enalapril"
    start = doc.text.index(needle)
    assert doc.pages_for_span(start, start + len(needle)) == (2, 2)


def test_a_span_crossing_a_page_break_reports_both_pages(guideline):
    """The reason page_start/page_end exist at all (migration 0003)."""
    doc = extract_pdf(guideline)

    start = doc.text.index("The target dose")
    end = doc.text.index("Review the patient") + len("Review the patient")

    assert doc.pages_for_span(start, end) == (2, 3)


def test_a_span_ending_exactly_on_a_page_boundary_does_not_claim_the_next_page(guideline):
    """Off-by-one guard. `end` is exclusive, so a chunk finishing at the last character
    of page 1 covers page 1 — claiming page 2 would cite text the chunk never held."""
    doc = extract_pdf(guideline)
    page_one = doc.pages[0]

    assert doc.pages_for_span(page_one.char_start, page_one.char_end) == (1, 1)


def test_the_whole_document_spans_every_page(guideline):
    doc = extract_pdf(guideline)
    assert doc.pages_for_span(0, len(doc.text)) == (1, 3)


# --- the failure mode this design removes ------------------------------------------


def test_repeated_headers_do_not_misattribute_pages(guideline):
    """The find() trap, stated as a test.

    "ESC Guidelines" is on all three pages. text.find() would resolve page 3's header to
    page 1 and cite the wrong page — plausibly, and with a real page number attached.
    Bisect over recorded offsets cannot: it never searches.
    """
    doc = extract_pdf(guideline)
    header = "ESC Guidelines"

    assert doc.text.count(header) == 3

    occurrences = []
    cursor = 0
    while (found := doc.text.find(header, cursor)) != -1:
        occurrences.append(found)
        cursor = found + 1

    resolved = [doc.pages_for_span(o, o + len(header))[0] for o in occurrences]
    assert resolved == [1, 2, 3], "each header must resolve to its own page"

    # And the trap itself: naive find() sends all three to page 1.
    naive = doc.text.find(header)
    assert doc.pages_for_span(naive, naive + len(header))[0] == 1
    assert occurrences[2] != naive, "the third header is not at the first occurrence"


# --- pages with no text ------------------------------------------------------------


def test_a_blank_page_keeps_numbering_aligned(tmp_path):
    """A blank page is still a page. Dropping it from the ledger would shift every
    later citation by one — the kind of error that is invisible until a clinician
    opens the PDF and finds the wrong content."""
    path = build_pdf(
        tmp_path / "gappy.pdf",
        [["Page one text"], [], ["Page three text"]],
    )
    doc = extract_pdf(path)

    assert doc.empty_pages == [2]
    assert [p.number for p in doc.pages] == [1, 2, 3]

    start = doc.text.index("Page three text")
    assert doc.pages_for_span(start, start + 5) == (3, 3)


def test_a_pdf_with_no_text_layer_is_rejected(tmp_path):
    """Silence is the danger. An image-only PDF extracts to nothing, ingests cleanly,
    and produces a document retrieval never returns — the clinician sees "no guidance
    found" and cannot tell that from "we never read your upload"."""
    path = tmp_path / "scanned.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.rect(100, 100, 200, 200, fill=1)  # ink, no text
    pdf.showPage()
    pdf.save()

    with pytest.raises(NoTextLayerError) as exc:
        extract_pdf(path)
    assert exc.value.page_count == 1
    assert "#13" in str(exc.value), "the error should point at the roadmap item that fixes it"


# --- bounds ------------------------------------------------------------------------


@pytest.mark.parametrize("start,end", [(-1, 5), (0, 0), (5, 4)])
def test_invalid_spans_are_rejected(guideline, start, end):
    doc = extract_pdf(guideline)
    with pytest.raises(ValueError):
        doc.pages_for_span(start, end)


def test_span_past_the_end_is_rejected(guideline):
    doc = extract_pdf(guideline)
    with pytest.raises(ValueError):
        doc.pages_for_span(0, len(doc.text) + 1)
