"""A search never crosses a sector, and the trap is set so only the filter can stop it.

The corpus now holds two unrelated bodies of text: clinical guidance and German
construction/procurement law. They share an embedder that has no concept of the
distinction, a lexical index over the same tokens, and an extractive answering layer that
quotes verbatim whatever retrieval hands it. That last part is why this is a safety test
rather than a relevance test. A statute reaching a clinical result set does not produce
an obviously-wrong answer — it produces a *correct-looking* one: a real quote, a real
§-reference, a real page number, from a real document. #19's verbatim check passes. The
citation resolves. Every guard in the system is satisfied, and a clinician is reading
building-fee law as if it were medical guidance.

**The decoy is the design.** A test where the other sector's chunk is merely irrelevant
proves nothing: it would pass with the sector filter deleted, because retrieval would have
ranked it last anyway. So each fixture plants a chunk in the *opposite* sector that is
engineered to win — its embedding is identical to the query vector (distance 0.0, the
nearest a hit can be) and its text contains the query's exact words, so the lexical half
ranks it first too. Both halves of the hybrid want to return it. The only thing that can
keep it out is the WHERE clause.

Each isolation test therefore carries a companion assertion: the decoy *is* reachable
from its own sector. Without that, "no decoy in the results" would also be satisfied by a
decoy that was never indexed, never embedded, or silently dropped — and the test would go
green while measuring nothing. That failure mode is the one this project keeps finding, so
it is asserted rather than assumed.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import insert, select

from app.core.config import get_settings
from app.core.vocabulary import IssuingOrg, Sector
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.ingestion import RegistrationRequest, register_version
from app.services.retrieval import hybrid_search, search

DIM = get_settings().embedding_dim

# An axis of its own, clear of every other fixture in the suite (enalapril=0, cancer=1,
# asthma=2, kidney=40, heart=41, orthogonal=DIM-1). The suite is additive — audit_log
# refuses TRUNCATE, so nothing resets between files — and a shared axis would let another
# module's chunks drift into these assertions.
SECTOR_AXIS = 42

MEDICAL_QUERY = "sepsis fluid resuscitation threshold"
LEGAL_QUERY = "Honorarzone Leistungsphase Bewertung"


class OneAxisEmbedder:
    """Everything lands on one axis, so every chunk is exactly as near as every other.

    Deliberately unable to tell the two sectors apart — that is the real-world condition
    being reproduced. multilingual-e5-large has no notion of "this is law, that is
    medicine"; it maps both to points in the same space, and "Leistungsphase" and
    "Phase III" are not far apart in it. An embedder that separated them would let this
    test pass for a reason that does not hold in production.
    """

    @property
    def dimension(self) -> int:
        return DIM

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        vector[SECTOR_AXIS] = 1.0
        return vector

    def embed_passages(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


@pytest.fixture
def embedder() -> OneAxisEmbedder:
    return OneAxisEmbedder()


async def _plant(session, *, org: str, sector: Sector, text: str, embedder):
    """One document in one sector, holding one chunk with the given text.

    `sector` is passed explicitly here rather than derived from `org`, because this is the
    fixture layer standing in for what ingestion writes — and one test below asserts that
    ingestion derives it correctly, which it could not do if the fixture derived it too.
    """
    marker = uuid.uuid4().hex[:10]
    document = Document(
        title=f"Sector Fixture {marker}",
        issuing_org=org,
        region="EU",
        sector=str(sector),
    )
    session.add(document)
    await session.flush()

    version = DocumentVersion(
        document_id=document.id,
        version_label="2024",
        file_hash=hashlib.sha256(marker.encode()).hexdigest(),
        storage_uri=f"/store/{marker}.pdf",
        status=VersionStatus.ACTIVE,
    )
    session.add(version)
    await session.flush()

    chunk_id = uuid.uuid4()
    await session.execute(
        insert(Chunk),
        [
            {
                "id": chunk_id,
                "document_version_id": version.id,
                "ordinal": 0,
                "page_start": 1,
                "page_end": 1,
                "section": "1",
                "content": text,
                "embedding": embedder.embed_passages([text])[0],
            }
        ],
    )
    await session.commit()
    return chunk_id


@pytest.fixture
async def decoys(session, embedder):
    """A legal chunk wearing a medical question's words, and a medical chunk wearing a
    legal one's. Each is the best possible hit for the query it must never answer."""
    legal_decoy = await _plant(
        session,
        org=IssuingOrg.BUNDESRECHT,
        sector=Sector.LEGAL,
        text=f"{MEDICAL_QUERY} — planted in the legal corpus to be retrieved by mistake",
        embedder=embedder,
    )
    medical_decoy = await _plant(
        session,
        org=IssuingOrg.ESC,
        sector=Sector.MEDICAL,
        text=f"{LEGAL_QUERY} — planted in the medical corpus to be retrieved by mistake",
        embedder=embedder,
    )
    return legal_decoy, medical_decoy


# --- the isolation itself -----------------------------------------------------------


@pytest.mark.parametrize("finder", [search, hybrid_search], ids=["vector", "hybrid"])
async def test_a_medical_search_never_returns_a_legal_chunk(session, embedder, decoys, finder):
    """Both paths, because they are two different queries.

    `search` builds its filter in SQLAlchemy and `hybrid_search` in hand-written SQL, and
    this project has already been bitten twice by a field added to one and forgotten in
    the other (see retrieval._search_hit). A guarantee that holds on one path and not the
    other is not a guarantee.
    """
    legal_decoy, _ = decoys

    hits = await finder(session, MEDICAL_QUERY, embedder, sector=Sector.MEDICAL, limit=200)
    returned = {hit.chunk_id for hit in hits}

    assert legal_decoy not in returned, (
        "a legal chunk came back from a medical search. It was engineered to win both "
        "halves of retrieval, so the sector filter is the only thing that was keeping it "
        "out — and it is not."
    )


@pytest.mark.parametrize("finder", [search, hybrid_search], ids=["vector", "hybrid"])
async def test_a_legal_search_never_returns_a_medical_chunk(session, embedder, decoys, finder):
    _, medical_decoy = decoys

    hits = await finder(session, LEGAL_QUERY, embedder, sector=Sector.LEGAL, limit=200)
    returned = {hit.chunk_id for hit in hits}

    assert medical_decoy not in returned, "a clinical chunk came back from a legal search"


@pytest.mark.parametrize("finder", [search, hybrid_search], ids=["vector", "hybrid"])
async def test_each_decoy_is_reachable_from_its_own_sector(session, embedder, decoys, finder):
    """The control, and the reason the two tests above mean anything.

    Both of them assert an absence, and an absence has many uninteresting explanations: the
    chunk was never written, the embedding was null, the fixture rolled back, the status
    was pending. Any of those would turn this file green while testing nothing. If the
    decoys are retrievable from their own side, the only remaining explanation for their
    absence from the other side is the filter.
    """
    legal_decoy, medical_decoy = decoys

    from_legal = {
        h.chunk_id for h in await finder(session, MEDICAL_QUERY, embedder, sector=Sector.LEGAL, limit=200)
    }
    from_medical = {
        h.chunk_id for h in await finder(session, LEGAL_QUERY, embedder, sector=Sector.MEDICAL, limit=200)
    }

    assert legal_decoy in from_legal, (
        "the legal decoy is not retrievable from the legal sector either — it was never "
        "really in the corpus, so the isolation tests above proved nothing"
    )
    assert medical_decoy in from_medical, "the medical decoy is not in the corpus at all"


# --- where a document's sector comes from -------------------------------------------


async def test_ingestion_derives_sector_from_the_issuing_organisation(session):
    """Filed under a legal organisation, it lands in the legal corpus — and the caller
    never said so.

    This is the property that keeps the filter honest. A sector the caller could set is a
    sector the caller could set wrongly, and the consequence would not be an error: HOAI
    filed as medical is simply quoted to clinicians forever, with correct provenance.
    `RegistrationRequest` has no sector field at all, which is the same control the query
    endpoint uses for actor_id — a field that does not exist cannot be filled in by
    accident.
    """
    marker = uuid.uuid4().hex[:10]
    version = await register_version(
        session,
        RegistrationRequest(
            title=f"HOAI Fixture {marker}",
            issuing_org=IssuingOrg.BUNDESRECHT,
            version_label="2023",
            file_hash=hashlib.sha256(marker.encode()).hexdigest(),
            storage_uri=f"/store/{marker}.pdf",
        ),
    )
    await session.commit()

    sector = (
        await session.execute(
            select(Document.sector)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(DocumentVersion.id == version.id)
        )
    ).scalar_one()

    assert sector == Sector.LEGAL


async def test_a_medical_organisation_still_lands_in_the_medical_corpus(session):
    """The other half of the same claim. Without it, a bug that wrote 'legal' for every
    document would pass the test above."""
    marker = uuid.uuid4().hex[:10]
    version = await register_version(
        session,
        RegistrationRequest(
            title=f"ESC Fixture {marker}",
            issuing_org=IssuingOrg.ESC,
            version_label="2024",
            file_hash=hashlib.sha256(marker.encode()).hexdigest(),
            storage_uri=f"/store/{marker}.pdf",
        ),
    )
    await session.commit()

    sector = (
        await session.execute(
            select(Document.sector)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(DocumentVersion.id == version.id)
        )
    ).scalar_one()

    assert sector == Sector.MEDICAL


def test_an_organisation_that_declares_no_sector_defaults_to_medical():
    """Every existing organisation predates the concept of a sector, and a new one added
    without a thought about it must not land in the legal corpus by omission."""
    for org in IssuingOrg:
        if org is IssuingOrg.BUNDESRECHT:
            assert org.sector is Sector.LEGAL
        else:
            assert org.sector is Sector.MEDICAL, f"{org} drifted out of the medical corpus"
