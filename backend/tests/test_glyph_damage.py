"""Unresolved glyphs, and why #19 cannot see them (#41).

Found by pointing the ingestion path at a real guideline for the first time — KDIGO 2012
CKD, 163 pages from kdigo.org. 11% of pages came back with `(cid:N)` where a
multiplication sign or a minus should be, and the CKD-EPI equation arrived as
`141(cid:2)min(SCr/k,1)a(cid:2)max(SCr/k,1)(cid:3)1.209`.

**The part that made it worth an issue rather than a bug fix.** The model never sees the
PDF; it is handed the chunk. Quoting that line character-for-character passes #19,
because the quote *is* a substring of the chunk. #19 validates quotes against chunks.
Nothing validated chunks against the source. A clinician would have seen a verbatim,
correctly-attributed, page-numbered citation of a formula with its operators deleted.

Most of this file runs against fixtures. Two tests need the real PDF and skip without it —
they are the ones that found the bug, and a synthetic `(cid:2)` proves only that a regex
works.
"""

import pathlib

import pytest

from app.services.extraction import UNRESOLVED_GLYPH

KDIGO = pathlib.Path(__file__).resolve().parents[1] / "storage" / "kdigo_2012_ckd.pdf"

# The line as it actually came out of the real PDF. Kept verbatim so the reason for all
# of this stays legible: this is the equation for estimating kidney function.
REAL_DAMAGE = "2009CKD-EPIcreatinineequation:141(cid:2)min(SCr/k,1)a(cid:2)max(SCr/k,1)(cid:3)1.209"


# --- the detector -------------------------------------------------------------------


def test_the_pattern_matches_what_pdfplumber_actually_emits():
    assert UNRESOLVED_GLYPH.findall(REAL_DAMAGE) == ["(cid:2)", "(cid:2)", "(cid:3)"]


@pytest.mark.parametrize(
    "text",
    [
        "Enalapril 2.5 mg once daily",
        "eGFR < 30 mL/min/1.73m2",
        "see Figure 3 (cidofovir)",  # the substring "cid" is not the marker
        "(cid) is not a glyph reference",
    ],
)
def test_ordinary_clinical_text_is_not_flagged(text):
    """A guard that fires on healthy text gets switched off by the first person it
    annoys, and then it is not a guard."""
    assert not UNRESOLVED_GLYPH.search(text)


# --- why this is not a cosmetic problem ---------------------------------------------


def test_a_corrupted_quote_passes_validation():
    """The reason #41 exists, demonstrated rather than asserted.

    #19 is the system's guarantee that a clinician sees only source text. Here it holds
    perfectly and is worth nothing: the model quotes the chunk faithfully, the chunk is
    wrong, and #19 has no way to know — it was never given the PDF either.
    """
    from app.services.validation import normalise

    chunk_content = REAL_DAMAGE
    quote_from_model = "141(cid:2)min(SCr/k,1)a(cid:2)max(SCr/k,1)(cid:3)1.209"

    # Exactly what validate_answer checks.
    assert normalise(quote_from_model) in normalise(chunk_content)

    # And what the clinician would be shown: a formula with every operator deleted,
    # verbatim, attributed, with a page number they can open to check.
    assert "(cid:" in quote_from_model


def test_the_damage_lands_on_operators_not_prose():
    """Why 11% understates it. The prose extracted cleanly; the mathematics did not, and
    the mathematics is the part a dose depends on."""
    assert "×" not in REAL_DAMAGE
    assert "min(SCr" in REAL_DAMAGE, "the words survived"
    assert REAL_DAMAGE.count("(cid:") == 3, "the operators did not"


# --- against the real guideline ------------------------------------------------------


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_the_real_guideline_is_damaged_and_extraction_says_so():
    """The measurement that opened #41. Skips rather than fails without the PDF: the file
    is 4.4 MB and not in the repo, and a test that invents its own damage would only be
    testing the regex.
    """
    from app.services.extraction import extract_pdf

    doc = extract_pdf(KDIGO)

    assert doc.glyph_damage, "the damage is real and extraction must report it"
    assert len(doc.damaged_pages) == 18
    assert doc.damage_ratio(len(doc.pages)) == pytest.approx(0.11, abs=0.01)
    assert sum(d.count for d in doc.glyph_damage) == 278

    # The page numbers are the point: an operator can open the PDF there and judge.
    assert 8 in doc.damaged_pages


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
def test_no_parser_can_fix_this_so_do_not_try():
    """All the damage comes from one embedded font that declares no ToUnicode CMap.

    Recorded as a test because the tempting next move — swap pdfplumber for another
    library — cannot work, and would cost a week to find out. The PDF says "draw glyph 2
    from this font" and never says which character glyph 2 is. A library that returned
    `×` there would be inferring from the glyph shape or the font name; it might be
    right. In a dosing formula, a confident guess is the failure we refuse everywhere
    else. (PyMuPDF is AGPL-3.0 besides — pdfplumber was chosen over it deliberately.)
    """
    import pdfplumber

    with pdfplumber.open(KDIGO) as pdf:
        page = pdf.pages[7]
        by_font: dict[str, list[str]] = {}
        for char in page.chars:
            by_font.setdefault(char["fontname"], []).append(char["text"])

    broken = {
        name: chars
        for name, chars in by_font.items()
        if any(c.startswith("(cid:") for c in chars)
    }

    assert len(broken) == 1, f"the damage is one font, not the parser: {list(broken)}"
    name, chars = next(iter(broken.items()))
    assert all(c.startswith("(cid:") for c in chars), (
        f"{name} resolves none of its glyphs — it carries no usable ToUnicode map"
    )
    assert len(by_font) > 5, "every other font on the page resolved fine"


# --- the gate itself -----------------------------------------------------------------


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
async def test_damaged_chunks_never_reach_the_corpus(session):
    """The guard, exercised rather than described.

    A mutation run caught this file with no test for the thing that matters: deleting the
    rejection from index_version broke nothing. Every test above proved the detector
    worked and none proved it was *used* — the same failure as a
    refuse_unless_eu_processing() that nothing calls. A guard nobody exercises is a guard
    that will be removed by whoever finds it confusing.

    Runs against the real 163-page guideline with a fake embedder: the vectors are
    irrelevant, what is under test is which chunks got written.
    """
    import uuid

    from sqlalchemy import select

    from app.models.chunk import Chunk
    from app.models.document import Document, DocumentVersion, VersionStatus
    from app.services.indexing import index_version
    from tests.test_indexing import FakeEmbedder

    marker = uuid.uuid4().hex[:8]
    document = Document(title=f"KDIGO CKD {marker}", issuing_org="ESC", region="EU")
    session.add(document)
    await session.flush()

    version = DocumentVersion(
        document_id=document.id,
        version_label=f"2012-{marker}",
        file_hash=marker.ljust(64, "0"),
        storage_uri=str(KDIGO),
        status=VersionStatus.PENDING,
    )
    session.add(version)
    await session.flush()

    result = await index_version(session, version.id, FakeEmbedder())
    await session.commit()

    assert result.rejected_chunks > 0, "the damaged chunks were written, not rejected"
    assert result.damaged_pages, "and the report says nothing about where"

    written = (
        await session.execute(
            select(Chunk.content).where(Chunk.document_version_id == version.id)
        )
    ).scalars().all()

    assert written, "the guideline is 89% clean; rejecting all of it would be worse"
    leaked = [c for c in written if "(cid:" in c]
    assert not leaked, (
        f"{len(leaked)} corrupted chunk(s) reached the corpus. #19 would bless every "
        f"quote from them: {leaked[0][:70]!r}"
    )
