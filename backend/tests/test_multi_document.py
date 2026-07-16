"""Retrieval and grouping across more than one document.

The corpus only ever held one guideline until this was written; hybrid_search, grouping by
issuing organisation, and "which document answers this" had all run at n=1. Exercised by
hand on a real two-document corpus (KDIGO + NICE), the routing was correct for
well-covered topics — a CKD question returned KDIGO 5/5, a heart-failure question returned
NICE. This pins that behaviour in CI with a deterministic embedder, so it cannot silently
regress.

Two facts this exposed on the real corpus, recorded as comments rather than asserted
because they are properties of the data, not the code:
  - Corpus imbalance biases retrieval toward the larger document. KDIGO (579 chunks) vs
    NICE (57) means a shared or weakly-covered topic pulls KDIGO chunks into the top-k.
    The retrieval *distance* carries the signal — clean matches at 0.12-0.15, weak ones at
    0.18 — but nothing currently uses it as a floor.
  - The RRF-fused top hit and the min-distance group can disagree, because hybrid_search
    fuses by rank while grouping orders by raw distance. Different orderings by design.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import insert

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.grouping import group_hits
from app.services.retrieval import hybrid_search

DIM = get_settings().embedding_dim

# Axes chosen well clear of the ones the rest of the suite uses (enalapril=0, cancer=1,
# asthma=2, orthogonal=DIM-1), so the shared additive corpus does not interfere: a chunk
# on axis 40 is orthogonal to every existing fixture, distance 1.0.
KIDNEY_AXIS = 40
HEART_AXIS = 41


class TwoTopicEmbedder:
    """A kidney axis and a heart axis. A text about one is orthogonal to the other, so
    'which document answers a kidney question' is arithmetic, not a hope about a model."""

    @property
    def dimension(self) -> int:
        return DIM

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * DIM
        low = text.lower()
        if "kidney" in low:
            v[KIDNEY_AXIS] = 1.0
        if "heart" in low:
            v[HEART_AXIS] = 1.0
        if not any(v):
            v[DIM - 2] = 1.0
        norm = sum(x * x for x in v) ** 0.5
        return [x / norm for x in v]

    def embed_passages(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


@pytest.fixture
def embedder() -> TwoTopicEmbedder:
    return TwoTopicEmbedder()


async def _ingest(session, *, title, org, texts, embedder):
    document = Document(title=f"{title} {uuid.uuid4().hex[:6]}", issuing_org=org, region="EU")
    session.add(document)
    await session.flush()
    marker = f"{document.id}"
    version = DocumentVersion(
        document_id=document.id,
        version_label="2020",
        file_hash=hashlib.sha256(marker.encode()).hexdigest(),
        storage_uri=f"/store/{marker}.pdf",
        status=VersionStatus.ACTIVE,
    )
    session.add(version)
    await session.flush()
    vectors = embedder.embed_passages(texts)
    await session.execute(
        insert(Chunk),
        [
            {
                "id": uuid.uuid4(),
                "document_version_id": version.id,
                "ordinal": i,
                "page_start": i + 1,
                "page_end": i + 1,
                "section": f"{i + 1}",
                "content": text,
                "embedding": vector,
            }
            for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))
        ],
    )
    await session.commit()
    return version


@pytest.fixture
async def two_guidelines(session, embedder):
    kidney = await _ingest(
        session,
        title="Kidney Guideline",
        org="KDIGO",
        texts=[
            "kidney disease is classified by glomerular filtration rate category",
            "kidney function should be monitored with serum creatinine",
            "kidney referral is indicated when function declines rapidly",
        ],
        embedder=embedder,
    )
    heart = await _ingest(
        session,
        title="Heart Guideline",
        org="NICE",
        texts=[
            "heart failure is treated first-line with an ACE inhibitor",
            "heart function is assessed by echocardiography",
            "heart transplantation is considered in end-stage disease",
        ],
        embedder=embedder,
    )
    return kidney, heart


def _mine(hits, *versions):
    ids = {v.id for v in versions}
    return [h for h in hits if h.document_version_id in ids]


# --- routing ------------------------------------------------------------------------


async def test_a_kidney_question_returns_the_kidney_guideline(session, embedder, two_guidelines):
    kidney, heart = two_guidelines

    hits = _mine(
        await hybrid_search(session, "how is kidney disease staged", embedder, limit=200),
        kidney,
        heart,
    )

    assert hits, "the kidney guideline is not searchable"
    assert hits[0].document_version_id == kidney.id, (
        f"a kidney question's top hit came from {hits[0].issuing_org}, not the kidney doc"
    )
    assert hits[0].issuing_org == "KDIGO"


async def test_a_heart_question_returns_the_heart_guideline(session, embedder, two_guidelines):
    kidney, heart = two_guidelines

    hits = _mine(
        await hybrid_search(session, "first-line treatment for heart failure", embedder, limit=200),
        kidney,
        heart,
    )

    assert hits
    assert hits[0].document_version_id == heart.id
    assert hits[0].issuing_org == "NICE"


async def test_the_wrong_document_does_not_crowd_the_top(session, embedder, two_guidelines):
    """The multi-document failure that matters: a question clearly about one topic must not
    return the other document's chunks above its own. With orthogonal topics this is
    absolute; on a real corpus it is a matter of degree (imbalance bias — see the module
    docstring)."""
    kidney, heart = two_guidelines

    hits = _mine(
        await hybrid_search(session, "kidney function monitoring", embedder, limit=200),
        kidney,
        heart,
    )
    top_kidney = [h for h in hits if h.document_version_id == kidney.id]
    top_heart = [h for h in hits if h.document_version_id == heart.id]

    assert top_kidney, "no kidney hits for a kidney query"
    if top_heart:
        assert hits.index(top_kidney[-1]) < hits.index(top_heart[0]), (
            "a heart chunk outranked a kidney chunk on a kidney query"
        )


# --- grouping across organisations --------------------------------------------------


async def test_grouping_separates_the_two_organisations(session, embedder, two_guidelines):
    kidney, heart = two_guidelines

    hits = _mine(
        await hybrid_search(session, "kidney and heart assessment", embedder, limit=200),
        kidney,
        heart,
    )
    groups = group_hits(hits)
    orgs = {g.issuing_org for g in groups}

    assert orgs == {"KDIGO", "NICE"}, f"grouping did not separate the organisations: {orgs}"
    for g in groups:
        assert len({h.document_version_id for h in g.hits}) == 1


async def test_each_document_keeps_its_own_provenance(session, embedder, two_guidelines):
    """A chunk from the kidney doc must never carry the heart doc's organisation or page.
    Cross-document contamination of provenance is the quiet version of the #42 family."""
    kidney, heart = two_guidelines

    hits = _mine(
        await hybrid_search(session, "kidney and heart", embedder, limit=200),
        kidney,
        heart,
    )
    for h in hits:
        if h.document_version_id == kidney.id:
            assert h.issuing_org == "KDIGO"
        elif h.document_version_id == heart.id:
            assert h.issuing_org == "NICE"
