"""Asking about a brand and finding the generic — through real retrieval (#16).

The gap this fills. `expand_query` was tested in isolation, `hybrid_search` calls it, and
nothing joined the two: no test had ever seeded an alias, put a passage about the generic
in the corpus, asked about the brand, and checked what came back. Every part was proven
and the path was not.

**Why this matters more here than a missing test usually would.** #16 measured the real
embedder against brand names and it scored *below chance* — margin −0.0010. The model
cannot get from "Renitec" to enalapril, and that is not a tokenisation gap the vector
half quietly covers; it is a fact nobody taught it. So expansion is not an optimisation
in this system, it is the only reason a brand-name query works at all. A silent
regression in it would look exactly like "the corpus has nothing on that".

**The aliases here are fixtures, and that distinction is load-bearing.** Renitec→enalapril
in a test is the same kind of object as the invented guideline text in
test_extraction_real.py: something whose only job is to exercise a mechanism. The
`drug_aliases` *table* stays empty until a real registry fills it, because a wrong alias
answers confidently about the wrong drug and `source` exists so every row is answerable
to a human. A fixture is not a seed.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import insert

from app.core.vocabulary import Sector
from app.core.config import get_settings
from app.models.alias import DrugAlias
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.retrieval import hybrid_search
from tests.test_retrieval import SEARCH_DEPTH, DirectionalEmbedder

DIM = get_settings().embedding_dim

# The passage a clinician asking about Renitec needs to find. It never says "Renitec" —
# which is the whole difficulty: no lexical match, and #16 measured that the vector half
# cannot bridge it either.
ENALAPRIL_DOSE = (
    "Enalapril should be initiated at 2.5 mg once daily in patients with renal "
    "impairment, and the dose titrated according to blood pressure response."
)


@pytest.fixture
def embedder() -> DirectionalEmbedder:
    return DirectionalEmbedder()


@pytest.fixture
async def brand_corpus(session, embedder):
    """One passage about enalapril, and an alias saying Renitec means enalapril."""
    marker = uuid.uuid4().hex[:8]
    document = Document(
        title=f"Brand Retrieval Guideline {marker}", issuing_org="ESC", region="EU"
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
                "page_start": 12,
                "page_end": 12,
                "section": "4.1 ACE inhibitors",
                "content": ENALAPRIL_DOSE,
                "embedding": embedder.embed_passages([ENALAPRIL_DOSE])[0],
            }
        ],
    )

    # A fixture, not a seed. See the module docstring: the real table stays empty until a
    # registry fills it, and `source` says so rather than pretending otherwise.
    alias = f"renitec-{marker}"
    session.add(
        DrugAlias(
            id=uuid.uuid4(),
            alias=alias,
            generic_name="enalapril",
            source="test fixture — NOT a registry, invented to exercise the mechanism",
        )
    )
    await session.commit()
    return alias, chunk_id


async def distance_to(session, query: str, embedder, chunk_id: uuid.UUID) -> float | None:
    """Cosine distance from this query to that chunk, as retrieval actually computed it.

    Distance, not rank, and not membership. Three drafts of this helper failed before the
    reason was clear:

    - *membership* is meaningless at SEARCH_DEPTH — a deep search returns nearly
      everything, so "is it in the results" is always yes;
    - *rank* is a coin flip here. `DirectionalEmbedder` maps any text with no keyword to
      one orthogonal axis, so every keyword chunk is exactly equidistant from an
      unexpanded brand query and their order is arbitrary tie-breaking. The draft that
      asserted rank got 0 both with and without the alias and looked like a real result.

    Distance is the thing the fake actually models: an expanded query points along
    enalapril's axis, an unexpanded one does not. That difference is arithmetic, not luck.
    """
    hits = await hybrid_search(session, query, embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)
    for hit in hits:
        if hit.chunk_id == chunk_id:
            return hit.distance
    return None


# --- the path that was never tested end to end --------------------------------------


async def test_asking_about_the_brand_finds_the_generic(session, embedder, brand_corpus):
    """The headline. Every piece of this was proven and the path never was.

    The query is the brand *alone*. That matters: the first draft asked for
    "{brand} dose in renal impairment", and those last three words are in the passage —
    so the lexical half matched them and the test would have passed with the aliases
    deleted. It measured the words around the brand, not the brand.
    """
    alias, chunk_id = brand_corpus

    via_brand = await distance_to(session, alias, embedder, chunk_id)
    via_generic = await distance_to(session, "enalapril", embedder, chunk_id)

    assert via_brand is not None, "the brand did not reach the passage at all"
    assert via_brand == pytest.approx(via_generic, abs=1e-6), (
        f"the brand landed {via_brand} from the passage and the generic {via_generic}. "
        "Expansion is supposed to make those the same question."
    )


async def test_the_alias_row_is_what_does_it(session, embedder, brand_corpus):
    """The control: the same brand, with the alias and without it.

    This is what makes the test above more than a tautology. Delete the row and the brand
    stops reaching the passage — so the alias table, and not something incidental, is
    what bridged the gap.

    **Not** "an unregistered brand finds nothing", which was the first draft. That is a
    claim about a real embedding model, and the embedder here is a fake whose vectors are
    chosen so the arithmetic is predictable — it cannot be below chance on brand names
    because it has never seen one. #16 measured that against the real weights (margin
    −0.0010) and this cannot add to it. A fake can prove the wiring; only the model can
    prove the model.
    """
    from sqlalchemy import delete

    from app.models.alias import DrugAlias

    alias, chunk_id = brand_corpus

    with_alias = await distance_to(session, alias, embedder, chunk_id)

    await session.execute(delete(DrugAlias).where(DrugAlias.alias == alias))
    await session.commit()
    without_alias = await distance_to(session, alias, embedder, chunk_id)

    assert with_alias < without_alias, (
        f"the brand landed {with_alias} from the passage with the alias and "
        f"{without_alias} without it. If removing the row changes nothing, the alias is "
        "not what found it and the headline test is measuring something else."
    )


async def test_the_generic_finds_it_without_needing_the_alias(session, embedder, brand_corpus):
    """Expansion must not be load-bearing for the ordinary case. If "enalapril" only
    works because of a table, the table has become a dependency for everything rather
    than a bridge for brand names.

    Rank is not asserted to be 0. The suite is additive and other modules seed their own
    enalapril passages, so "top hit" is a bet on how many neighbours exist — the first
    draft made that bet and lost at rank 2. What matters is that it is reached, and that
    nothing was substituted to reach it.
    """
    from app.services.synonyms import expand_query, load_aliases

    _, chunk_id = brand_corpus

    assert await distance_to(session, "enalapril", embedder, chunk_id) is not None
    assert not expand_query("enalapril", await load_aliases(session)).was_expanded


async def test_the_brand_is_kept_so_a_guideline_naming_it_still_matches(
    session, embedder, brand_corpus
):
    """Why expansion appends rather than replaces.

    Replacing the brand with the generic — the obvious implementation — would drop the
    only token that matches a guideline which *does* name the brand. It trades one blind
    spot for another. Here the expanded query still carries the brand, so both kinds of
    passage stay reachable.
    """
    from app.services.synonyms import expand_query, load_aliases

    alias, _ = brand_corpus
    expanded = expand_query(f"{alias} dose", await load_aliases(session))

    assert alias in expanded.expanded, "the brand survived expansion"
    assert "enalapril" in expanded.expanded, "and the generic was added"
    assert expanded.applied == {alias: "enalapril"}, "and the substitution is inspectable"


async def test_an_expansion_nobody_can_see_is_one_nobody_can_correct(
    session, embedder, brand_corpus
):
    """"Why did asking about Renitec return enalapril?" must have an answer. `applied` is
    it — surfaced rather than hidden, because a wrong alias is a wrong-drug bug and the
    only way to find it is for someone to read what was substituted."""
    from app.services.synonyms import expand_query, load_aliases

    alias, _ = brand_corpus
    aliases = await load_aliases(session)

    assert expand_query(f"{alias} dose", aliases).was_expanded
    assert not expand_query("enalapril dose", aliases).was_expanded, (
        "no substitution, nothing to report"
    )
