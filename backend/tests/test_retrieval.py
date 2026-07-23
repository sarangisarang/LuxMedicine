"""Vector search tests (#14).

Vectors here come from the fake embedder, so these say nothing about whether the model
ranks clinical text well — that is measured in test_embedding_real.py, against the real
weights. What they pin is the part that is ours: which versions are searchable, and
whether a hit carries enough to cite.

Every assertion is scoped to the version the test created. The suite is additive by
design — audit_log rejects TRUNCATE, so there is no reset between tests — which means
`search(...) == []` is never a safe claim: it passes only until a neighbouring test
seeds a chunk that happens to match. Ask whether *this* version came back.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import insert, select, update

from app.core.vocabulary import Sector
from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.retrieval import search
from app.services.supersession import supersede

DIM = get_settings().embedding_dim

# Deliberately far larger than any test needs. The suite is additive — audit_log rejects
# TRUNCATE, so nothing resets — and it now spans files: test_hybrid_search seeds its own
# "enalapril" passages. Any fixed limit is a bet on how many neighbours happen to exist,
# and that bet loses silently the day someone adds a fixture. Search deep, then filter to
# the version this test created.
SEARCH_DEPTH = 500


class DirectionalEmbedder:
    """Vectors chosen so that similarity is predictable.

    Each keyword owns one axis. A text containing it points along that axis, so "the
    query mentioning enalapril matches the enalapril chunk" is arithmetic rather than a
    hope about a model.
    """

    AXES = {"enalapril": 0, "cancer": 1, "asthma": 2}

    @property
    def dimension(self) -> int:
        return DIM

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        for keyword, axis in self.AXES.items():
            if keyword in text.lower():
                vector[axis] = 1.0
        if not any(vector):
            vector[DIM - 1] = 1.0  # orthogonal to every keyword axis
        norm = sum(v * v for v in vector) ** 0.5
        return [v / norm for v in vector]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def embedder() -> DirectionalEmbedder:
    return DirectionalEmbedder()


async def make_version(
    session,
    *,
    title: str,
    org: str,
    label: str,
    status: VersionStatus,
    texts: list[str],
    embedder: DirectionalEmbedder,
    superseded_by: uuid.UUID | None = None,
) -> DocumentVersion:
    document = Document(title=f"{title} {uuid.uuid4().hex[:6]}", issuing_org=org, region="EU")
    session.add(document)
    await session.flush()

    marker = f"{document.id}-{label}"
    version = DocumentVersion(
        document_id=document.id,
        version_label=label,
        file_hash=hashlib.sha256(marker.encode()).hexdigest(),
        storage_uri=f"/store/{marker}.pdf",
        status=status,
        superseded_by=superseded_by,
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
                "section": f"{i + 1} Section",
                "content": text,
                "embedding": vector,
            }
            for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))
        ],
    )
    await session.commit()
    return version


# --- what is searchable ------------------------------------------------------------


def versions_in(hits) -> set:
    return {hit.document_version_id for hit in hits}


async def test_a_query_finds_the_matching_chunk(session, embedder):
    version = await make_version(
        session,
        title="Active Guideline",
        org="ESC",
        label="2024",
        status=VersionStatus.ACTIVE,
        texts=["The target dose of enalapril is 20 mg.", "Asthma is managed with inhalers."],
        embedder=embedder,
    )

    hits = await search(session, "enalapril dose", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)

    # Both of this version's chunks come back — a wide limit returns everything it can.
    # What matters is the order: the matching one first, the asthma one behind it.
    mine = [h for h in hits if h.document_version_id == version.id]
    assert len(mine) == 2
    assert "enalapril" in mine[0].content
    assert mine[0].distance < mine[1].distance


async def test_pending_versions_are_never_searched(session, embedder):
    """#10's whole point. A pending version's chunks may not exist, and returning
    partial guidance is worse than returning none."""
    version = await make_version(
        session,
        title="Pending Guideline",
        org="AHA",
        label="p-2024",
        status=VersionStatus.PENDING,
        texts=["Pending guidance about enalapril dosing."],
        embedder=embedder,
    )

    hits = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)
    assert version.id not in versions_in(hits)


async def test_archived_versions_are_excluded_by_default(session, embedder):
    version = await make_version(
        session,
        title="Archived Guideline",
        org="NICE",
        label="a-2015",
        status=VersionStatus.ARCHIVED,
        texts=["Old guidance about enalapril dosing."],
        embedder=embedder,
    )

    hits = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)
    assert version.id not in versions_in(hits)


async def test_archived_versions_are_reachable_on_request(session, embedder):
    """Not a convenience. This is how an answer given in 2024 gets re-checked in 2026
    against the edition it actually cited."""
    version = await make_version(
        session,
        title="Recheck Guideline",
        org="WHO",
        label="r-2015",
        status=VersionStatus.ARCHIVED,
        texts=["Historical guidance about enalapril dosing."],
        embedder=embedder,
    )

    default = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)
    assert version.id not in versions_in(default)

    widened = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, include_archived=True, sector=Sector.MEDICAL)
    assert version.id in versions_in(widened)


async def test_pending_stays_excluded_even_with_archived_included(session, embedder):
    """include_archived widens the search to superseded guidance, not to guidance that
    was never indexed. Those are different things and only one of them exists."""
    version = await make_version(
        session,
        title="Still Pending",
        org="KDIGO",
        label="sp-2024",
        status=VersionStatus.PENDING,
        texts=["Unindexed guidance about enalapril."],
        embedder=embedder,
    )

    hits = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, include_archived=True, sector=Sector.MEDICAL)
    assert version.id not in versions_in(hits)


async def test_withdrawn_stays_excluded_even_with_archived_included(session, embedder):
    """#49 is a licence firewall, and a firewall with an exception is not one. A version
    withdrawn because its licence forbids indexing must be unreachable by EVERY path — the
    default search, and the deliberate include_archived widening that a supersession recheck
    uses. archived means "superseded, still inspectable on purpose"; withdrawn means "must not
    answer, at all", and conflating them would let a KDIGO recheck resurface KDIGO."""
    version = await make_version(
        session,
        title="Withdrawn For Licence",
        org="KDIGO",
        label="wl-2012",
        status=VersionStatus.WITHDRAWN,
        texts=["Prohibited guidance about enalapril dosing."],
        embedder=embedder,
    )

    default = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)
    assert version.id not in versions_in(default)

    widened = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, include_archived=True, sector=Sector.MEDICAL)
    assert version.id not in versions_in(widened), "withdrawn must not resurface with archived"


# --- what a hit carries ------------------------------------------------------------


async def test_a_hit_carries_everything_a_citation_needs(session, embedder):
    """Citation needs organisation, edition and page. If retrieval does not return them,
    #18 has to go looking — and the value it finds might not be the one that was
    retrieved."""
    version = await make_version(
        session,
        title="Citable Guideline",
        org="ESC",
        label="2021",
        status=VersionStatus.ACTIVE,
        texts=["The target dose of enalapril is 20 mg."],
        embedder=embedder,
    )

    hits = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)
    hit = next(h for h in hits if h.document_version_id == version.id)

    assert hit.issuing_org == "ESC"
    assert hit.version_label == "2021"
    assert hit.page_start == 1 and hit.page_end == 1
    assert hit.section == "1 Section"
    assert 0.0 <= hit.distance <= 2.0


async def test_results_are_ordered_by_distance(session, embedder):
    version = await make_version(
        session,
        title="Ordered Guideline",
        org="ACC",
        label="2024",
        status=VersionStatus.ACTIVE,
        texts=[
            "The target dose of enalapril is 20 mg.",
            "Cancer screening starts at 45.",
            "Asthma inhaler technique matters.",
        ],
        embedder=embedder,
    )

    hits = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)

    assert [h.distance for h in hits] == sorted(h.distance for h in hits), "global ordering"

    mine = [h for h in hits if h.document_version_id == version.id]
    assert "enalapril" in mine[0].content.lower(), "within this version, the match leads"


async def test_a_superseded_version_is_flagged(session, embedder):
    """#17's input, exercised through the real supersession path rather than by writing
    the column directly.

    An archived edition reached on purpose must still announce that it is not current.
    Otherwise "I asked for history" and "this is the guidance" arrive looking identical,
    and the clinician cannot tell which one they are reading.
    """
    document = Document(
        title=f"Evolving Guideline {uuid.uuid4().hex[:6]}", issuing_org="ESC", region="EU"
    )
    session.add(document)
    await session.flush()

    async def add(label: str) -> DocumentVersion:
        marker = f"{document.id}-{label}"
        version = DocumentVersion(
            document_id=document.id,
            version_label=label,
            file_hash=hashlib.sha256(marker.encode()).hexdigest(),
            storage_uri=f"/store/{marker}.pdf",
            status=VersionStatus.ACTIVE,
        )
        session.add(version)
        await session.flush()
        [vector] = embedder.embed_passages([f"{label} guidance about enalapril."])
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
                    "content": f"{label} guidance about enalapril.",
                    "embedding": vector,
                }
            ],
        )
        return version

    old = await add("2021")
    new = await add("2023")
    await session.commit()

    await supersede(session, version_id=old.id, superseded_by_id=new.id)
    await session.commit()

    hits = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, include_archived=True, sector=Sector.MEDICAL)
    flags = {h.document_version_id: h.is_superseded for h in hits}

    assert flags[old.id] is True, "an archived edition must say so when reached deliberately"
    assert flags[new.id] is False, "the current edition is not superseded"


async def test_limit_is_respected(session, embedder):
    await make_version(
        session,
        title="Many Chunks",
        org="IDSA",
        label="2024",
        status=VersionStatus.ACTIVE,
        texts=[f"Enalapril note {i}." for i in range(12)],
        embedder=embedder,
    )

    assert len(await search(session, "enalapril", embedder, limit=5, sector=Sector.MEDICAL)) == 5


async def test_an_empty_corpus_returns_nothing_rather_than_failing(session, embedder):
    assert await search(session, "something nobody wrote about", embedder, limit=10, sector=Sector.MEDICAL) is not None


async def test_a_superseded_chunk_leaves_the_index_but_stays_in_the_table(session, embedder):
    """A row a later reading replaced must stop competing with its replacement.

    The case this exists for: augment_tables is insert-only (0008 forbids deleting a cited
    chunk), so completing a truncated label ADDS the corrected row beside the flawed one. Both
    were then retrievable, and "ii. Systolic ≥160 mm Hg or" could out-rank "Systolic ≥160 mm Hg
    or diastolic ≥100 mm Hg" — half a threshold, quoted verbatim and cited correctly.

    Both halves are asserted, because either alone is the wrong outcome: gone from retrieval,
    still present in the table so the audit trail resolves.
    """
    version = await make_version(
        session,
        title="Superseded Rows",
        org="ESC",
        label="2026",
        status=VersionStatus.ACTIVE,
        texts=["Enalapril truncated reading", "Enalapril complete reading"],
        embedder=embedder,
    )
    await session.commit()

    rows = (
        await session.execute(
            select(Chunk.id, Chunk.content)
            .where(Chunk.document_version_id == version.id)
            .order_by(Chunk.ordinal)
        )
    ).all()
    old_id, new_id = rows[0][0], rows[1][0]

    before = await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)
    assert old_id in {h.chunk_id for h in before}, "the fixture is not retrievable to begin with"

    await session.execute(update(Chunk).where(Chunk.id == old_id).values(superseded_by=new_id))
    await session.commit()

    after = {h.chunk_id for h in await search(session, "enalapril", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)}
    assert old_id not in after, "a superseded chunk is still answering"
    assert new_id in after, "the replacement must remain retrievable"

    still_there = (
        await session.execute(select(Chunk.id).where(Chunk.id == old_id))
    ).scalar_one_or_none()
    assert still_there == old_id, "the superseded chunk was deleted — the audit trail needs it"


async def test_the_hybrid_path_excludes_superseded_chunks_too(session, embedder):
    """Two queries, two filters. This project has twice shipped a field added to the ORM path
    and forgotten in the hand-written hybrid SQL (see retrieval._search_hit), and a guarantee
    that holds on one path is not a guarantee."""
    from app.services.retrieval import hybrid_search

    version = await make_version(
        session,
        title="Superseded Hybrid",
        org="ACC",
        label="2026",
        status=VersionStatus.ACTIVE,
        texts=["Enalapril hybrid truncated", "Enalapril hybrid complete"],
        embedder=embedder,
    )
    await session.commit()

    rows = (
        await session.execute(
            select(Chunk.id).where(Chunk.document_version_id == version.id).order_by(Chunk.ordinal)
        )
    ).all()
    old_id, new_id = rows[0][0], rows[1][0]

    await session.execute(update(Chunk).where(Chunk.id == old_id).values(superseded_by=new_id))
    await session.commit()

    hits = {
        h.chunk_id
        for h in await hybrid_search(session, "enalapril hybrid", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)
    }
    assert old_id not in hits, "the hybrid path still returns superseded chunks"
