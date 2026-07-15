"""A cited passage cannot be deleted (#40).

#27's export reports a passage it cannot resolve. This makes the situation unreachable
rather than merely reported — the same move as 0001's append-only triggers, and for the
same reason: "only reachable via direct SQL" is the threat those exist for, not a
mitigation.

Note what these do **not** assert: that deletion breaks the chain. It never did. The chain
hashes audit rows and has never touched the corpus, so it kept verifying over a trail that
could no longer show what it relied on. That is what made this worth a guard of its own.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.audit import verify_chain
from app.services.audit_export import export_audit
from app.services.pipeline import answer_query
from tests.test_pipeline import ScriptedExtractor

# This module's own clinic. The suite is additive and shares one database, so two
# modules sharing a clinic would share a chain -- and a chain test passing because
# of another module's rows proves nothing.
CLINIC = "clinic-cited-chunk-protection"



# Carvedilol, not bisoprolol. test_pipeline and test_audit_export both seed bisoprolol
# passages, and the suite is additive — this module's chunks stopped reaching the top of
# retrieval once they had company, so `ask()` cited someone else's document and the
# cascade test deleted one with nothing to protect. Third time this lesson has arrived.
DOSE = "The target dose of carvedilol is 25 mg twice daily in heart failure."
MONITORING = "Review blood pressure after each carvedilol dose increase."

DIM = get_settings().embedding_dim


class CarvedilolEmbedder:
    @property
    def dimension(self) -> int:
        return DIM

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        vector[0 if "carvedilol" in text.lower() else DIM - 1] = 1.0
        return vector

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def embedder() -> CarvedilolEmbedder:
    return CarvedilolEmbedder()


@pytest.fixture
async def corpus(session, embedder):
    document = Document(
        title=f"Protected Guideline {uuid.uuid4().hex[:6]}", issuing_org="ESC", region="EU"
    )
    session.add(document)
    await session.flush()

    marker = f"{document.id}-protected"
    version = DocumentVersion(
        document_id=document.id,
        version_label="2021",
        file_hash=hashlib.sha256(marker.encode()).hexdigest(),
        storage_uri=f"/store/{marker}.pdf",
        status=VersionStatus.ACTIVE,
    )
    session.add(version)
    await session.flush()

    texts = [DOSE, MONITORING]
    vectors = embedder.embed_passages(texts)
    await session.execute(
        insert(Chunk),
        [
            {
                "id": uuid.uuid4(),
                "document_version_id": version.id,
                "ordinal": i,
                "page_start": 45 + i,
                "page_end": 45 + i,
                "section": "3.2 Beta blockers",
                "content": text,
                "embedding": vector,
            }
            for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))
        ],
    )
    await session.commit()
    return document, version


async def ask(session, embedder, *, actor: str):
    return await answer_query(
        session,
        question="carvedilol dose?",
        actor_id=actor,
        clinic_id=CLINIC,
        embedder=embedder,
        extractor=ScriptedExtractor([DOSE]),
    )


# --- the guard ---------------------------------------------------------------------


async def test_a_cited_chunk_cannot_be_deleted(session, embedder, corpus):
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    answered = await ask(session, embedder, actor=actor)
    cited = answered.hits[0].chunk_id

    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("DELETE FROM chunks WHERE id = :id"), {"id": cited})
    assert "cited by the audit trail" in str(exc.value)
    await session.rollback()

    survivor = (await session.execute(select(Chunk).where(Chunk.id == cited))).scalar_one_or_none()
    assert survivor is not None


async def test_a_chunk_the_trail_never_read_is_still_deletable(session, embedder):
    """The guard protects evidence, not the corpus. An unread passage — a bad upload, say
    — must still be removable, or a mistake becomes permanent."""
    document = Document(
        title=f"Unread Guideline {uuid.uuid4().hex[:6]}", issuing_org="AHA", region="US"
    )
    session.add(document)
    await session.flush()
    marker = f"{document.id}-unread"
    version = DocumentVersion(
        document_id=document.id,
        version_label="draft",
        file_hash=hashlib.sha256(marker.encode()).hexdigest(),
        storage_uri=f"/store/{marker}.pdf",
        status=VersionStatus.PENDING,
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
                "section": None,
                "content": "A passage nobody ever retrieved.",
                "embedding": [0.0] * (DIM - 1) + [1.0],
            }
        ],
    )
    await session.commit()

    await session.execute(text("DELETE FROM chunks WHERE id = :id"), {"id": chunk_id})
    await session.commit()

    assert (await session.execute(select(Chunk).where(Chunk.id == chunk_id))).scalar_one_or_none() is None


async def test_deleting_the_document_is_refused_through_the_cascade(session, embedder, corpus):
    """The path that actually mattered.

    Nobody deletes a chunk directly. `DELETE FROM documents` cascades to versions and on
    to chunks, and the row trigger aborts the whole transaction from three tables away —
    so the guideline survives because its passages are evidence.
    """
    document, _ = corpus
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    await ask(session, embedder, actor=actor)
    document_id = document.id

    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
    assert "cited by the audit trail" in str(exc.value)
    await session.rollback()

    still_there = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    assert still_there is not None


async def test_truncate_is_refused_separately(session, embedder, corpus):
    """TRUNCATE bypasses row-level triggers — the same hole 0001 had to plug on
    audit_log, and it would have taken the entire corpus with it."""
    await ask(session, embedder, actor=f"dr-{uuid.uuid4().hex[:6]}")

    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("TRUNCATE chunks CASCADE"))
    assert "cannot be truncated" in str(exc.value)
    await session.rollback()

    assert (await session.execute(select(func.count()).select_from(Chunk))).scalar_one() > 0


# --- the escape hatch --------------------------------------------------------------


async def test_a_takedown_clears_the_text_and_the_export_says_so(session, embedder, corpus):
    """The deliberate way out, and its deliberate consequence.

    UPDATE is not blocked, so a copyright demand is served by clearing the passage: the
    row and its id survive, the trail still resolves, and #27's export reports the quote
    no longer matching. That is the truth of that situation — the guideline text is gone
    and the record says the corpus moved.

    GDPR is not why one would do this. A published guideline is not personal data; the
    erasable side of that line is `queries` (#5), which already works.
    """
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    answered = await ask(session, embedder, actor=actor)
    cited = next(h.chunk_id for h in answered.hits if DOSE in h.content)

    await session.execute(
        text("UPDATE chunks SET content = :empty WHERE id = :id"),
        {"empty": "[removed on legal demand]", "id": cited},
    )
    await session.commit()

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    [entry] = export.entries
    assert entry.sources_complete, "the row survived, so the trail still resolves"
    assert not entry.corpus_matches_the_record, "and the export says the text is no longer there"
    assert not export.is_evidential


# --- what the chain was never going to catch ---------------------------------------


async def test_the_chain_verifies_either_way(session, embedder, corpus):
    """The finding that made #40 necessary, kept as a test.

    Deleting a cited chunk would have left every verification passing. The chain hashes
    audit rows; it has never hashed the corpus. If this ever starts failing, the chain has
    grown a property it does not have — and the guard's justification would need rereading.
    """
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    answered = await ask(session, embedder, actor=actor)

    assert await verify_chain(session, clinic_id=CLINIC) > 0

    with pytest.raises(DBAPIError):
        await session.execute(
            text("DELETE FROM chunks WHERE id = :id"), {"id": answered.hits[0].chunk_id}
        )
    await session.rollback()

    assert await verify_chain(session, clinic_id=CLINIC) > 0
