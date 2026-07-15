"""Section detection and chunking tests (#9)."""

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.chunking import MAX_CHARS, chunk_document, heading_of
from app.services.extraction import Line, extract_pdf


def line(text: str, *, size: float = 10.0, bold: bool = False) -> Line:
    return Line(text=text, char_start=0, char_end=len(text), page=1, font_size=size, is_bold=bold)


BODY = 10.0


# --- the test this module exists for -----------------------------------------------


def test_a_dose_is_never_read_as_a_heading():
    """The load-bearing test.

    "2.5 mg may be used..." and "2.1 Pharmacological therapy" are identical to a regex:
    both open with a decimal at the start of a line. In a clinical corpus, decimals at
    the start of lines are usually doses. Reading one as a heading breaks the text at
    exactly the wrong place — severing a dose from the context qualifying it.
    """
    dose = line("2.5 mg may be used as a starting dose in frail patients.", size=BODY)
    assert heading_of(dose, body_font_size=BODY) is None

    heading = line("2.1 Pharmacological therapy", size=13.0, bold=True)
    assert heading_of(heading, body_font_size=BODY) == "2.1 Pharmacological therapy"


@pytest.mark.parametrize(
    "text",
    [
        "2.5 mg daily",  # short enough to pass the length rule
        "10 mg twice daily",
        "0.5 mL subcutaneously",
        "20 mmol/L threshold",
        "5 kg weight gain",
        "12 weeks of therapy",
        "140 mmHg systolic",
        "30 min after dosing",
    ],
)
def test_measurements_are_rejected_even_when_typeset_like_headings(text):
    """The unit guard is a belt to typography's braces. A bold, short, numbered line is
    still a measurement if a unit follows the number — and emphasis on a dose is
    exactly what a guideline does to make it stand out."""
    assert heading_of(line(text, size=14.0, bold=True), body_font_size=BODY) is None


# --- heading detection -------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2 Treatment", "2 Treatment"),
        ("2.1 Pharmacological therapy", "2.1 Pharmacological therapy"),
        ("3.4.1 Recommendations for ACE inhibitors", "3.4.1 Recommendations for ACE inhibitors"),
    ],
)
def test_numbered_headings_are_detected_when_typeset_as_headings(text, expected):
    assert heading_of(line(text, size=13.0, bold=True), body_font_size=BODY) == expected


def test_bold_at_body_size_still_counts():
    """Some publishers set sub-headings bold without enlarging them."""
    assert heading_of(line("4.2 Monitoring", size=BODY, bold=True), body_font_size=BODY) is not None


def test_body_text_that_happens_to_be_numbered_is_not_a_heading():
    """Typography is the discriminator. Same text, body type — not a heading."""
    assert heading_of(line("2.1 Pharmacological therapy", size=BODY), body_font_size=BODY) is None


def test_long_numbered_prose_is_not_a_heading():
    long = line(
        "2.1 The committee reviewed the evidence and concluded that treatment should be "
        "initiated promptly in all eligible patients without delay.",
        size=13.0,
        bold=True,
    )
    assert heading_of(long, body_font_size=BODY) is None


def test_unnumbered_text_is_not_a_heading():
    """Conservative by design: no number, no label. A missing section costs a little;
    a wrong one is worse than none (#25)."""
    assert heading_of(line("Introduction", size=16.0, bold=True), body_font_size=BODY) is None


# --- chunking over a real PDF ------------------------------------------------------


@pytest.fixture
def guideline(tmp_path) -> Path:
    path = tmp_path / "guideline.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(60, 800, "2 Treatment")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(60, 775, "Treatment should begin promptly.")

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(60, 745, "2.1 Pharmacological therapy")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(60, 720, "The target dose of enalapril is 20 mg twice daily.")
    pdf.drawString(60, 705, "2.5 mg may be used as a starting dose in frail patients.")
    pdf.showPage()

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(60, 800, "2.2 Monitoring")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(60, 775, "Review renal function after two weeks.")
    pdf.showPage()
    pdf.save()
    return path


def test_chunks_carry_their_section(guideline):
    chunks = chunk_document(extract_pdf(guideline))
    sections = [c.section for c in chunks]

    assert "2 Treatment" in sections
    assert "2.1 Pharmacological therapy" in sections
    assert "2.2 Monitoring" in sections


def test_the_dose_line_stays_inside_its_section(guideline):
    """End to end: the "2.5 mg" line must not have opened a section of its own."""
    chunks = chunk_document(extract_pdf(guideline))

    holding = [c for c in chunks if "2.5 mg may be used" in c.text]
    assert len(holding) == 1
    assert holding[0].section == "2.1 Pharmacological therapy"

    assert not any(c.section and c.section.startswith("2.5") for c in chunks)


def test_chunk_text_matches_its_recorded_span(guideline):
    """The same invariant that carries test_extraction: if a chunk's text and its span
    disagree, every citation drawn from it is wrong while everything still looks fine."""
    doc = extract_pdf(guideline)

    for chunk in chunk_document(doc):
        assert doc.text[chunk.char_start : chunk.char_end] == chunk.text


def test_pages_are_resolved_through_the_ledger(guideline):
    doc = extract_pdf(guideline)
    chunks = chunk_document(doc)

    monitoring = next(c for c in chunks if c.section == "2.2 Monitoring")
    assert monitoring.page_start == 2
    assert monitoring.page_end == 2

    for chunk in chunks:
        assert chunk.page_end >= chunk.page_start  # the ck_chunk_page_span constraint


def test_ordinals_are_sequential(guideline):
    chunks = chunk_document(extract_pdf(guideline))
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


# --- packing and overlap -----------------------------------------------------------


@pytest.fixture
def long_section(tmp_path) -> Path:
    """One section long enough to force the packer to split it."""
    path = tmp_path / "long.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, 800, "5 Evidence")
    pdf.setFont("Helvetica", 10)

    y = 780
    for i in range(45):
        pdf.drawString(50, y, f"Sentence {i:02d} describing the evidence base in detail.")
        y -= 14
        if y < 60:
            pdf.showPage()
            pdf.setFont("Helvetica", 10)
            y = 800
    pdf.showPage()
    pdf.save()
    return path


def test_a_long_section_is_split_into_several_chunks(long_section):
    chunks = chunk_document(extract_pdf(long_section))
    assert len(chunks) > 1
    assert all(c.section == "5 Evidence" for c in chunks)


def test_consecutive_chunks_overlap(long_section):
    """Overlap is the belt to the braces on clinical context: even when a break lands
    badly, a qualifying clause still appears in one chunk alongside what it qualifies."""
    chunks = chunk_document(extract_pdf(long_section))
    assert len(chunks) >= 2

    overlapping = [
        (a, b) for a, b in zip(chunks, chunks[1:], strict=False) if b.char_start < a.char_end
    ]
    assert overlapping, "no consecutive pair overlapped — the carried tail was lost"


def test_chunks_stay_within_the_hard_cap(long_section):
    """A chunk that grows without bound embeds badly and cites vaguely."""
    for chunk in chunk_document(extract_pdf(long_section)):
        assert len(chunk.text) <= MAX_CHARS + 200, f"chunk {chunk.ordinal} ran to {len(chunk.text)}"


def test_the_packer_always_advances(long_section):
    """Guard against the overlap carrying an entire buffer, which would leave the next
    chunk starting where the last one did and loop forever."""
    chunks = chunk_document(extract_pdf(long_section))
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b.char_start > a.char_start
        assert b.char_end > a.char_end
