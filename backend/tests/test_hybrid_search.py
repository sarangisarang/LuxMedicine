"""Hybrid search tests (#15).

The fake embedder here is deliberately *bad* at telling drugs apart — it reproduces the
condition measured against the real model, where five ACE inhibitors scored within 0.055
of each other. That flatness is the problem hybrid search exists to solve, so the tests
need it present rather than assumed away.

Assertions are about *which drug* came back, not which row. The suite is additive —
audit_log rejects TRUNCATE, so there is no reset — and this fixture seeds an identical
passage per test, so several equally-correct lisinopril chunks accumulate. Demanding a
specific id would make the test a lottery over rows that are all the right answer.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import insert

from app.core.vocabulary import Sector
from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.retrieval import hybrid_search, search

DIM = get_settings().embedding_dim

DRUGS = ["enalapril", "lisinopril", "ramipril", "captopril", "perindopril"]


class MisleadingEmbedder:
    """A model that ranks the wrong drug first, every time.

    The first attempt at this fixture was flat-ish: identical passages plus a 0.02
    whisper of drug identity. It looked adversarial and was not — that whisper was the
    only difference between the vectors, so it decided the ordering, and the hybrid tests
    passed with the lexical half switched off entirely. They proved nothing.

    So this one is unambiguous: every ACE query lands nearest CONFOUNDER, whichever drug
    was asked about. Vector search cannot answer these questions at all; if a test passes,
    the lexical half is why.

    That is a caricature of the real model, which gets top-1 right — but only by 0.04,
    across five drugs inside a 0.055 band. At that margin the ordering is not a judgement
    the embedding is making, and "usually right" is not a property to build a dose lookup
    on.
    """

    CONFOUNDER = "lisinopril"

    @property
    def dimension(self) -> int:
        return DIM

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        vector = [0.0] * DIM

        if not any(drug in lowered for drug in DRUGS) and "ace inhibitor" not in lowered:
            vector[DIM - 1] = 1.0  # unrelated text, orthogonal to everything ACE
            return vector

        vector[0] = 1.0  # "about ACE inhibitors"
        # Every ACE passage and every ACE query leans the same way: toward the confounder.
        # A query for enalapril is therefore *closest* to the lisinopril passage.
        vector[1] = 0.15 if self.CONFOUNDER in lowered else 0.0

        norm = sum(v * v for v in vector) ** 0.5
        return [v / norm for v in vector]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        # Queries lean toward the confounder regardless of what they ask about.
        vector = [0.0] * DIM
        lowered = text.lower()
        if not any(drug in lowered for drug in DRUGS) and "ace inhibitor" not in lowered:
            vector[DIM - 1] = 1.0
            return vector
        vector[0] = 1.0
        vector[1] = 0.15
        norm = sum(v * v for v in vector) ** 0.5
        return [v / norm for v in vector]


@pytest.fixture
def embedder() -> MisleadingEmbedder:
    return MisleadingEmbedder()


@pytest.fixture
async def ace_corpus(session, embedder) -> dict[str, uuid.UUID]:
    """One passage per ACE inhibitor, differing only by drug name and dose."""
    document = Document(
        title=f"ACE Guideline {uuid.uuid4().hex[:6]}", issuing_org="ESC", region="EU"
    )
    session.add(document)
    await session.flush()

    marker = f"{document.id}-hybrid"
    version = DocumentVersion(
        document_id=document.id,
        version_label="2024",
        file_hash=hashlib.sha256(marker.encode()).hexdigest(),
        storage_uri=f"/store/{marker}.pdf",
        status=VersionStatus.ACTIVE,
    )
    session.add(version)
    await session.flush()

    doses = {"enalapril": 20, "lisinopril": 35, "ramipril": 10, "captopril": 50, "perindopril": 8}
    texts = [
        f"The target dose of {drug} is {dose} mg daily in patients with heart failure."
        for drug, dose in doses.items()
    ]
    vectors = embedder.embed_passages(texts)

    chunk_ids: dict[str, uuid.UUID] = {}
    rows = []
    for i, (drug, text, vector) in enumerate(zip(doses, texts, vectors, strict=True)):
        chunk_id = uuid.uuid4()
        chunk_ids[drug] = chunk_id
        rows.append(
            {
                "id": chunk_id,
                "document_version_id": version.id,
                "ordinal": i,
                "page_start": i + 1,
                "page_end": i + 1,
                "section": "1 Pharmacological therapy",
                "content": text,
                "embedding": vector,
            }
        )

    await session.execute(insert(Chunk), rows)
    await session.commit()
    return chunk_ids


# --- the test this module exists for -----------------------------------------------


async def test_the_lexical_half_separates_drugs_the_embedding_cannot(session, embedder, ace_corpus):
    """The measured problem, and the fix, in one test.

    The embedder here cannot rank the five apart — as the real model very nearly cannot,
    scoring them inside 0.055. Hybrid search does not need it to: "enalapril" is a token
    that is present in exactly one passage.
    """
    hits = await hybrid_search(session, "target dose of enalapril", embedder, limit=1, sector=Sector.MEDICAL)

    assert len(hits) == 1
    assert "enalapril" in hits[0].content
    assert not any(other in hits[0].content for other in DRUGS if other != "enalapril")
    assert hits[0].found_by_lexical, "the lexical half is what could tell these apart"


async def test_vector_only_search_returns_the_wrong_drug(session, embedder, ace_corpus):
    """The control. Without it, the test above proves only that *something* worked.

    Same corpus, same embedder, vector search alone: the confounder leads for a query
    that never mentions it. This is what #18 would be handed — a passage about the wrong
    drug's dose, ranked first, reading as relevant as the right one.
    """
    hits = await search(session, "target dose of enalapril", embedder, limit=100, sector=Sector.MEDICAL)
    ours = [h for h in hits if h.chunk_id in set(ace_corpus.values())]

    assert len(ours) == 5, "all five drugs come back, undifferentiated"
    assert embedder.CONFOUNDER in ours[0].content, "vector search leads with the wrong drug"
    assert "enalapril" not in ours[0].content


@pytest.mark.parametrize("drug", DRUGS)
async def test_every_drug_is_reachable_by_name(session, embedder, ace_corpus, drug):
    hits = await hybrid_search(session, f"target dose of {drug}", embedder, limit=1, sector=Sector.MEDICAL)

    assert drug in hits[0].content
    assert not any(other in hits[0].content for other in DRUGS if other != drug)


# --- fusion behaviour --------------------------------------------------------------


async def test_a_hit_reports_which_half_found_it(session, embedder, ace_corpus):
    hits = await hybrid_search(session, "target dose of enalapril", embedder, limit=10, sector=Sector.MEDICAL)
    top = hits[0]

    assert top.found_by_lexical
    assert top.rrf_score is not None and top.rrf_score > 0


async def test_a_query_with_no_lexical_overlap_still_works(session, embedder, ace_corpus):
    """A Georgian query shares no tokens with English text, so the lexical half
    contributes nothing. That is expected, not broken — the vector half carries those,
    and the fusion must not collapse when one side returns empty."""
    hits = await hybrid_search(session, "ჰიპერტენზიის მკურნალობა", embedder, limit=5, sector=Sector.MEDICAL)

    assert isinstance(hits, list)
    for hit in hits:
        assert hit.found_by_vector or hit.found_by_lexical


async def test_lexical_only_hits_can_surface(session, embedder, ace_corpus):
    """A chunk the embedding ranks nowhere can still reach the answer on the strength of
    an exact term. This is the whole asymmetry hybrid search buys."""
    hits = await hybrid_search(session, "perindopril", embedder, limit=3, sector=Sector.MEDICAL)

    assert any("perindopril" in h.content for h in hits)


async def test_results_are_ordered_by_the_fusion(session, embedder, ace_corpus):
    hits = await hybrid_search(session, "target dose of enalapril", embedder, limit=10, sector=Sector.MEDICAL)
    scores = [h.rrf_score for h in hits]
    assert scores == sorted(scores, reverse=True)


# --- the status filter must survive the rewrite ------------------------------------


async def test_hybrid_search_respects_pending(session, embedder):
    """The SQL was hand-written, so #10's guarantee is re-asserted rather than assumed
    to have carried over from search()."""
    document = Document(
        title=f"Pending Hybrid {uuid.uuid4().hex[:6]}", issuing_org="AHA", region="US"
    )
    session.add(document)
    await session.flush()
    marker = f"{document.id}-pending"
    version = DocumentVersion(
        document_id=document.id,
        version_label="p1",
        file_hash=hashlib.sha256(marker.encode()).hexdigest(),
        storage_uri=f"/store/{marker}.pdf",
        status=VersionStatus.PENDING,
    )
    session.add(version)
    await session.flush()
    [vector] = embedder.embed_passages(["The target dose of enalapril is 20 mg."])
    await session.execute(
        insert(Chunk),
        [
            {
                "id": uuid.uuid4(),
                "document_version_id": version.id,
                "ordinal": 0,
                "page_start": 1,
                "page_end": 1,
                "section": None,
                "content": "The target dose of enalapril is 20 mg.",
                "embedding": vector,
            }
        ],
    )
    await session.commit()

    hits = await hybrid_search(session, "enalapril", embedder, limit=50, sector=Sector.MEDICAL)
    assert version.id not in {h.document_version_id for h in hits}

    widened = await hybrid_search(session, "enalapril", embedder, limit=50, include_archived=True, sector=Sector.MEDICAL)
    assert version.id not in {h.document_version_id for h in widened}


# --- free text must not break the query parser -------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "patient's dose",  # apostrophe
        "enalapril & lisinopril",  # tsquery operator
        "dose | mg",  # another one
        "what about (this)?",  # parentheses
        "!!!",  # nothing but punctuation
        "  ",  # whitespace
        "<script>alert(1)</script>",
        "enalapril:*",
    ],
)
async def test_free_text_never_reaches_the_tsquery_parser_raw(session, embedder, ace_corpus, hostile):
    """websearch_to_tsquery, not to_tsquery. A clinician types what they type, and
    to_tsquery raises a syntax error on a stray ampersand — a search box that 500s on an
    apostrophe is not a search box."""
    hits = await hybrid_search(session, hostile, embedder, limit=5, sector=Sector.MEDICAL)
    assert isinstance(hits, list)
