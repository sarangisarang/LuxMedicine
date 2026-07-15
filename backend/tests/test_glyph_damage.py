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
import uuid as uuid_mod

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

    # Against page.chars, not against a number I once wrote down. A glyph is a property
    # of the PDF: no extraction strategy can change how many there are. Column-aware
    # extraction (#42) briefly reported 292 — 14 characters read twice at band boundaries,
    # because crop() takes everything that *intersects* the box. Pinning the literal 278
    # would have caught that as "the fixture changed"; comparing to the source catches it
    # as what it was.
    import pdfplumber

    with pdfplumber.open(KDIGO) as pdf:
        truth = sum(
            1 for page in pdf.pages for c in page.chars if c["text"].startswith("(cid:")
        )

    assert sum(d.count for d in doc.glyph_damage) == truth, (
        "extraction reports a different number of unresolved glyphs than the PDF has — "
        "characters are being read twice, or dropped"
    )

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

    **Never commits.** The first version did, and 163 pages of chunks flooded the shared
    corpus — test_a_query_finds_the_matching_chunk started expecting 2 hits and getting 1,
    because a neighbouring test's passage had been pushed out of the result window by
    several hundred pages of nephrology. The suite is additive by design and this is the
    largest thing anyone has put in it. Asserting inside the transaction and rolling back
    proves exactly the same thing and leaves nothing behind.
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

    # See the docstring: committing this would put 163 pages of nephrology into every
    # other test's corpus.
    await session.rollback()


# --- the half that was missing ------------------------------------------------------


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
async def test_the_hole_is_visible_to_whoever_reads_the_answer(session):
    """The half of #41 the first commit left out.

    The chunks were correctly refused and nothing remembered. So a clinician asking about
    the CKD-EPI equation would get the surrounding prose about eGFR estimation, without
    the equation, and with nothing saying a page was missing. **The answer looks
    complete.** That is "the guideline does not say" and "we could not read the page where
    it says it" producing the same output — the confusion #20 exists to prevent, one layer
    down, created by the guard meant to prevent it.

    The damage was loud to the operator running ingestion and silent to the clinician
    reading the result, which is the wrong way round.
    """
    import uuid

    from sqlalchemy import select

    from app.models.document import Document, DocumentVersion, VersionStatus
    from app.services.indexing import index_version
    from tests.test_indexing import FakeEmbedder

    marker = uuid.uuid4().hex[:8]
    document = Document(title=f"KDIGO Hole {marker}", issuing_org="ESC", region="EU")
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

    await index_version(session, version.id, FakeEmbedder())
    await session.flush()

    stored = (
        await session.execute(
            select(DocumentVersion.unreadable_pages).where(DocumentVersion.id == version.id)
        )
    ).scalar_one()

    assert stored, "the damage did not survive ingestion; the corpus just got quieter"
    assert 8 in stored, "and it must name the page, so someone can open the PDF there"

    await session.rollback()


def test_an_unmeasured_version_is_not_a_clean_one():
    """NULL and [] are different claims. Empty means measured and clean; NULL means nobody
    looked — a version indexed before 0013. Collapsing them would let 'we never checked'
    read as 'we checked and it was fine', which is the same trade this whole issue is
    about."""
    from app.services.retrieval import SearchHit

    unmeasured = SearchHit(
        chunk_id=uuid_mod.uuid4(),
        document_version_id=uuid_mod.uuid4(),
        document_id=uuid_mod.uuid4(),
        document_title="Old Guideline",
        issuing_org="ESC",
        version_label="2019",
        section="1",
        page_start=1,
        page_end=1,
        content="x",
        distance=0.1,
        is_superseded=False,
        superseding_version_label=None,
    )

    assert unmeasured.unreadable_pages is None, "not [] — nobody measured this version"


def test_the_group_shows_the_holes_of_the_document_it_cites():
    from app.schemas.answer import SourceGroup

    group = SourceGroup(
        issuing_org="ESC",
        version_label="2012",
        document_version_id=uuid_mod.uuid4(),
        citations=[
            {
                "chunk_id": uuid_mod.uuid4(),
                "document_version_id": uuid_mod.uuid4(),
                "document_title": "KDIGO 2012 CKD",
                "issuing_org": "ESC",
                "version_label": "2012",
                "page_start": 30,
                "page_end": 30,
                "section": "4.1",
                "quote": "GFR should be estimated from serum creatinine.",
            }
        ],
        unreadable_pages=[7, 8, 10],
    )

    assert group.has_unreadable_pages
    assert group.unreadable_pages == [7, 8, 10]


def test_a_clean_document_says_nothing():
    """The warning must be absent when there is nothing to warn about, or it becomes
    furniture and stops being read."""
    from app.schemas.answer import SourceGroup

    group = SourceGroup(
        issuing_org="ESC",
        version_label="2021",
        document_version_id=uuid_mod.uuid4(),
        citations=[
            {
                "chunk_id": uuid_mod.uuid4(),
                "document_version_id": uuid_mod.uuid4(),
                "document_title": "Clean Guideline",
                "issuing_org": "ESC",
                "version_label": "2021",
                "page_start": 1,
                "page_end": 1,
                "section": "1",
                "quote": "Bisoprolol should be initiated at 1.25 mg once daily.",
            }
        ],
    )

    assert not group.has_unreadable_pages
    assert group.unreadable_pages == []


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
async def test_the_damage_survives_the_whole_path_to_the_answer(session):
    """Database -> retrieval -> grouping -> the object a clinician is handed.

    A mutation run found three of the four links untested: dropping unreadable_pages from
    the SearchHit, from the group, or from the SourceGroup broke nothing. Every test above
    checked one end or the other — indexing writes it, a hand-built SourceGroup shows it —
    and none walked the middle. A field that four functions must pass along is a field
    three of them can quietly stop passing.

    Runs inside one transaction: index, search what was just indexed, roll back. Nothing
    reaches the shared corpus.
    """
    import uuid

    from app.models.document import Document, DocumentVersion, VersionStatus
    from app.services.answering import assemble
    from app.services.grouping import group_hits
    from app.services.indexing import index_version
    from app.services.retrieval import hybrid_search
    from tests.test_indexing import FakeEmbedder

    marker = uuid.uuid4().hex[:8]
    document = Document(title=f"KDIGO Path {marker}", issuing_org="ESC", region="EU")
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

    embedder = FakeEmbedder()
    await index_version(session, version.id, embedder)
    await session.flush()

    try:
        hits = await hybrid_search(session, "chronic kidney disease", embedder, limit=200)
        mine = [h for h in hits if h.document_version_id == version.id]
        assert mine, "the freshly indexed version is not searchable"

        # 1. the hit carries it out of the database
        assert mine[0].unreadable_pages, "retrieval dropped it"
        assert 8 in mine[0].unreadable_pages

        # 2. grouping carries it
        groups = group_hits(mine)
        assert groups[0].unreadable_pages, "grouping dropped it"

        # 3. and the object the clinician is handed says so
        from app.services.answering import ExtractionResult, SelectedQuote

        quote = mine[0].content[:60]
        answer = assemble(
            "chronic kidney disease",
            mine[:1],
            ExtractionResult(quotes=[SelectedQuote(source=1, quote=quote)]),
            prompt="(test)",
            model="fake",
            query_language="en",
        )
        [group] = answer.payload.groups
        assert group.has_unreadable_pages, (
            "the answer reached the clinician with no sign that this document has holes"
        )
        assert 8 in group.unreadable_pages
    finally:
        await session.rollback()


@pytest.mark.skipif(not KDIGO.exists(), reason="needs storage/kdigo_2012_ckd.pdf")
async def test_both_retrieval_paths_carry_it(session):
    """`search()` and `hybrid_search()` build SearchHits from separate queries.

    A mutation run deleted the field from one of the two constructions and every test
    still passed: the test above goes through hybrid_search, so the vector-only path was
    uncovered. Two code paths that must agree are two code paths that will not, and the
    one nobody exercises is the one that drifts.
    """
    import uuid

    from app.models.document import Document, DocumentVersion, VersionStatus
    from app.services.indexing import index_version
    from app.services.retrieval import hybrid_search, search
    from tests.test_indexing import FakeEmbedder

    marker = uuid.uuid4().hex[:8]
    document = Document(title=f"KDIGO Paths {marker}", issuing_org="ESC", region="EU")
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

    embedder = FakeEmbedder()
    await index_version(session, version.id, embedder)
    await session.flush()

    try:
        for finder in (search, hybrid_search):
            hits = await finder(session, "chronic kidney disease", embedder, limit=400)
            mine = [h for h in hits if h.document_version_id == version.id]
            assert mine, f"{finder.__name__} did not reach the version"
            assert mine[0].unreadable_pages, f"{finder.__name__} dropped unreadable_pages"
            assert 8 in mine[0].unreadable_pages
    finally:
        await session.rollback()
