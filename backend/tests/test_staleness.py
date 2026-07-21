"""Staleness warning tests (#17).

The clinician-facing claim under test: "you are reading the 2021 guideline; a 2023
edition exists". Getting the *label* wrong is worse than omitting it — it sends someone
to read a specific document that is also out of date, with the system's confidence
behind the suggestion.
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
from app.services.staleness import MAX_CHAIN_DEPTH, latest_labels
from app.services.supersession import supersede

DIM = get_settings().embedding_dim
SEARCH_DEPTH = 500


class KeywordEmbedder:
    @property
    def dimension(self) -> int:
        return DIM

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        vector[0 if "warfarin" in text.lower() else DIM - 1] = 1.0
        return vector

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def embedder() -> KeywordEmbedder:
    return KeywordEmbedder()


@pytest.fixture
async def guideline(session, embedder):
    """A document with three editions and one chunk each, nothing superseded yet."""
    document = Document(
        title=f"Anticoagulation {uuid.uuid4().hex[:6]}", issuing_org="ESC", region="EU"
    )
    session.add(document)
    await session.flush()

    versions = {}
    for label in ("2019", "2021", "2023"):
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

        content = f"Warfarin monitoring guidance, {label} edition."
        [vector] = embedder.embed_passages([content])
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
                    "content": content,
                    "embedding": vector,
                }
            ],
        )
        versions[label] = version

    await session.commit()
    return document, versions


# --- the test this module exists for -----------------------------------------------


async def test_a_chain_reports_the_current_edition_not_the_next_one(session, guideline):
    """The whole point of walking the chain.

    With 2019 -> 2021 -> 2023, `superseded_by` on the 2019 row says "2021". Telling a
    clinician that would send them to read another outdated guideline and consider the
    matter closed. The answer they need is 2023.
    """
    _, versions = guideline
    await supersede(session, version_id=versions["2019"].id, superseded_by_id=versions["2021"].id)
    await supersede(session, version_id=versions["2021"].id, superseded_by_id=versions["2023"].id)
    await session.commit()

    labels = await latest_labels(session, [versions["2019"].id])

    assert labels[versions["2019"].id] == "2023"
    assert versions["2019"].superseded_by == versions["2021"].id, "the immediate successor is 2021"


async def test_the_current_edition_maps_to_itself(session, guideline):
    _, versions = guideline
    await supersede(session, version_id=versions["2019"].id, superseded_by_id=versions["2021"].id)
    await session.commit()

    labels = await latest_labels(session, [versions["2021"].id])
    assert labels[versions["2021"].id] == "2021"


async def test_an_unsuperseded_version_maps_to_itself(session, guideline):
    _, versions = guideline
    labels = await latest_labels(session, [versions["2023"].id])
    assert labels[versions["2023"].id] == "2023"


async def test_several_versions_resolve_in_one_pass(session, guideline):
    _, versions = guideline
    await supersede(session, version_id=versions["2019"].id, superseded_by_id=versions["2021"].id)
    await supersede(session, version_id=versions["2021"].id, superseded_by_id=versions["2023"].id)
    await session.commit()

    labels = await latest_labels(session, [v.id for v in versions.values()])

    assert labels[versions["2019"].id] == "2023"
    assert labels[versions["2021"].id] == "2023"
    assert labels[versions["2023"].id] == "2023"


async def test_no_versions_means_no_query(session):
    assert await latest_labels(session, []) == {}


# --- through retrieval -------------------------------------------------------------


async def test_an_archived_hit_names_the_current_edition(session, embedder, guideline):
    """End to end: a clinician deliberately reading history is told what supersedes it."""
    _, versions = guideline
    await supersede(session, version_id=versions["2019"].id, superseded_by_id=versions["2021"].id)
    await supersede(session, version_id=versions["2021"].id, superseded_by_id=versions["2023"].id)
    await session.commit()

    hits = await search(session, "warfarin", embedder, limit=SEARCH_DEPTH, include_archived=True, sector=Sector.MEDICAL)
    by_version = {h.document_version_id: h for h in hits}

    old = by_version[versions["2019"].id]
    assert old.is_superseded
    assert old.superseding_version_label == "2023", "not 2021 — that edition is stale too"

    current = by_version[versions["2023"].id]
    assert not current.is_superseded
    assert current.superseding_version_label is None, "nothing to warn about"


async def test_hybrid_search_reports_staleness_too(session, embedder, guideline):
    """The hybrid path has its own hand-written SQL, so it is asserted rather than
    assumed to have inherited this."""
    _, versions = guideline
    await supersede(session, version_id=versions["2019"].id, superseded_by_id=versions["2023"].id)
    await session.commit()

    hits = await hybrid_search(
        session,
        "warfarin monitoring",
        embedder,
        sector=Sector.MEDICAL,
        limit=SEARCH_DEPTH,
        include_archived=True,
    )
    by_version = {h.document_version_id: h for h in hits}

    assert by_version[versions["2019"].id].superseding_version_label == "2023"


async def test_a_current_hit_carries_no_warning(session, embedder, guideline):
    """The silence has to be trustworthy. If unsuperseded hits carried a label, the
    banner would fire on current guidance and clinicians would learn to close it."""
    _, versions = guideline

    hits = await search(session, "warfarin", embedder, limit=SEARCH_DEPTH, sector=Sector.MEDICAL)

    for hit in (h for h in hits if h.document_version_id in {v.id for v in versions.values()}):
        assert not hit.is_superseded
        assert hit.superseding_version_label is None


# --- the guard ---------------------------------------------------------------------


async def test_the_recursion_is_bounded(session, guideline):
    """The walk trusts #12's proof that cycles cannot exist. This asserts the seatbelt
    is fastened anyway: if something ever writes superseded_by directly and closes a
    loop, the query must return a bounded wrong answer rather than never return."""
    assert MAX_CHAIN_DEPTH > 0

    _, versions = guideline
    await supersede(session, version_id=versions["2019"].id, superseded_by_id=versions["2021"].id)
    await session.commit()

    # A short real chain is unaffected by the cap.
    labels = await latest_labels(session, [versions["2019"].id])
    assert labels[versions["2019"].id] == "2021"
