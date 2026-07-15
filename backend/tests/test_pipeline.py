"""Query-flow tests (#26): retrieve, extract, validate, record.

The pipeline is where every earlier guarantee has to survive contact with the others.
These use a fake extractor — the real one is unproven and costs money — so what they pin
is the wiring: that the audit records what was shown, that a broken extractor still
leaves a trail, and that the chain verifies afterwards.
"""

import asyncio
import hashlib
import threading
import time
import uuid

import pytest
from sqlalchemy import insert, select

from app.core.config import get_settings
from app.models.audit import AuditLog
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.models.query import Query
from app.schemas.answer import NoAnswerReason
from app.services.answering import ExtractionResult, SelectedQuote
from app.services.audit import salted_hash, verify_chain
from app.services.pipeline import answer_query

DIM = get_settings().embedding_dim

# Bisoprolol, not enalapril. The suite is additive and several other modules seed their
# own enalapril passages — this fixture's chunks stopped reaching the top of retrieval
# once they had company, and a quote outside the shown window is indistinguishable from a
# fabricated one. A drug nobody else uses keeps this module's corpus findable.
DOSE = "The target dose of bisoprolol is 10 mg once daily in heart failure."
MONITORING = "Review heart rate and blood pressure after two weeks of bisoprolol therapy."


class SimpleEmbedder:
    @property
    def dimension(self) -> int:
        return DIM

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        vector[0 if "bisoprolol" in text.lower() else DIM - 1] = 1.0
        return vector

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class ScriptedExtractor:
    """Quotes the spans it was given, resolving `source` by finding them.

    Quotes are named, not passage numbers. The first version hard-coded `source=1` and
    passed alone but failed in the full suite: the corpus is shared (audit_log rejects
    TRUNCATE, so nothing resets), other tests seed their own enalapril passages, and
    passage 1 is whatever retrieval ranked first that day. Betting on that is betting on
    every other test's fixtures.

    Resolving the index from the quote is also what a real extractor does — it picks the
    passage the span is in, rather than being told which one to look at.
    """

    model = "scripted-extractor"

    def __init__(self, quotes: list[str] | None = None, *, raises: Exception | None = None) -> None:
        self._quotes = quotes
        self._raises = raises
        self.calls: list[tuple[str, list[str]]] = []

    def extract(self, question: str, passages: list[str]):
        self.calls.append((question, list(passages)))
        if self._raises is not None:
            raise self._raises
        if self._quotes is None:
            return None

        selections = []
        for quote in self._quotes:
            index = next(
                (i for i, p in enumerate(passages, start=1) if quote in p),
                # Not found: the quote is fabricated, so attribute it to the first
                # passage — which is what a hallucinating model does.
                1,
            )
            selections.append(SelectedQuote(source=index, quote=quote))
        return ExtractionResult(quotes=selections)


@pytest.fixture
def embedder() -> SimpleEmbedder:
    return SimpleEmbedder()


@pytest.fixture
async def corpus(session, embedder):
    """One active ESC version with two chunks."""
    document = Document(
        title=f"Pipeline Guideline {uuid.uuid4().hex[:6]}", issuing_org="ESC", region="EU"
    )
    session.add(document)
    await session.flush()

    marker = f"{document.id}-pipeline"
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
                "section": "2.1 Pharmacological therapy",
                "content": text,
                "embedding": vector,
            }
            for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))
        ],
    )
    await session.commit()
    return version


# --- the happy path ----------------------------------------------------------------


async def test_a_question_produces_an_answer_and_a_trail(session, embedder, corpus):
    extractor = ScriptedExtractor([DOSE])

    answered = await answer_query(
        session, question="bisoprolol dose?", actor_id="dr-001", embedder=embedder, extractor=extractor
    )

    assert answered.payload.groups
    [citation] = [c for g in answered.payload.groups for c in g.citations]
    assert citation.quote == DOSE

    # Provenance came from the hit carrying that quote, not from the model.
    source_hit = next(h for h in answered.hits if DOSE in h.content)
    assert citation.page_start == source_hit.page_start
    assert citation.chunk_id == source_hit.chunk_id
    assert answered.audit_seq > 0


async def test_the_audit_records_what_the_clinician_was_shown(session, embedder, corpus):
    """Not the model's raw output. #19 runs first, so the trail is evidence of what we
    said — not of what we caught."""
    extractor = ScriptedExtractor([DOSE, "Bisoprolol is contraindicated in pregnancy."])

    answered = await answer_query(
        session, question="bisoprolol dose?", actor_id="dr-001", embedder=embedder, extractor=extractor
    )

    row = (await session.execute(select(AuditLog).where(AuditLog.seq == answered.audit_seq))).scalar_one()
    quotes = [c["quote"] for g in row.response["groups"] for c in g["citations"]]

    assert quotes == [DOSE], "the fabricated quote must not be in the trail as if we showed it"
    assert row.response["rejected_citations"] == 1


async def test_the_audit_records_everything_retrieved_not_only_what_was_quoted(
    session, embedder, corpus
):
    """"Which sources did the system look at" is what a dispute asks. Recording only the
    quoted ones would hide the passages it read and discarded."""
    extractor = ScriptedExtractor([DOSE])

    answered = await answer_query(
        session, question="bisoprolol dose?", actor_id="dr-001", embedder=embedder, extractor=extractor
    )

    row = (await session.execute(select(AuditLog).where(AuditLog.seq == answered.audit_seq))).scalar_one()

    assert len(row.retrieved_chunk_ids) == len(answered.hits)
    assert len(answered.hits) > 1, "more was retrieved than was quoted"


async def test_the_question_is_stored_erasably_and_hashed_into_the_chain(session, embedder, corpus):
    """The GDPR boundary (#5) reached through the real flow: the text lives in `queries`
    where it can be redacted; only its salted hash is in the chain.

    This asserted a plain `sha256(question)` until #28 measured what that was worth —
    an unsalted hash of a guessable question is a lookup key for it, 20 of 20 recovered
    in 0.3 ms. The assertion below is now the inverse of what it used to be.
    """
    question = "45yo male, reduced EF, on a beta blocker — target bisoprolol dose?"
    extractor = ScriptedExtractor([DOSE])

    answered = await answer_query(
        session, question=question, actor_id="dr-001", embedder=embedder, extractor=extractor
    )

    query = (await session.execute(select(Query).where(Query.id == answered.query_id))).scalar_one()
    row = (await session.execute(select(AuditLog).where(AuditLog.seq == answered.audit_seq))).scalar_one()

    assert query.text == question
    assert query.text_salt is not None, "an unsalted question is one that cannot be erased"
    assert row.query_hash == salted_hash(query.text_salt, question)
    assert row.query_hash != hashlib.sha256(question.encode()).hexdigest(), (
        "a plain hash here would survive erasure as a working oracle for the question"
    )
    assert question not in str(row.response), "the question must not leak into the response blob"


async def test_the_chain_still_verifies_after_a_real_query(session, embedder, corpus):
    extractor = ScriptedExtractor([DOSE])

    await answer_query(
        session, question="bisoprolol dose?", actor_id="dr-001", embedder=embedder, extractor=extractor
    )

    assert await verify_chain(session) > 0


# --- failure is auditable ----------------------------------------------------------


async def test_a_broken_extractor_still_leaves_a_trail(session, embedder, corpus):
    """A trail that only contains successes is not a trail.

    The clinician asked and got nothing; that is a fact about us, and the row says so
    with an error. Same distinction NoAnswerReason draws (#20).
    """
    extractor = ScriptedExtractor(raises=RuntimeError("the model API fell over"))

    answered = await answer_query(
        session, question="bisoprolol dose?", actor_id="dr-001", embedder=embedder, extractor=extractor
    )

    row = (await session.execute(select(AuditLog).where(AuditLog.seq == answered.audit_seq))).scalar_one()

    assert row.error is not None and "fell over" in row.error
    assert answered.payload.no_answer_reason is NoAnswerReason.SOURCES_DO_NOT_ANSWER
    assert await verify_chain(session) > 0, "a failed query must not break the chain"


async def test_a_refusal_leaves_a_clean_trail(session, embedder, corpus):
    """A safety refusal is not our malfunction — no error is recorded, but the query is."""
    extractor = ScriptedExtractor(None)  # a refusal

    answered = await answer_query(
        session, question="bisoprolol dose?", actor_id="dr-001", embedder=embedder, extractor=extractor
    )

    row = (await session.execute(select(AuditLog).where(AuditLog.seq == answered.audit_seq))).scalar_one()

    assert row.error is None
    assert answered.payload.no_answer_reason is NoAnswerReason.SOURCES_DO_NOT_ANSWER


async def test_an_empty_corpus_says_the_corpus_is_silent(session, embedder):
    extractor = ScriptedExtractor([])

    answered = await answer_query(
        session,
        question="a topic nobody has ever written about anywhere",
        actor_id="dr-001",
        embedder=embedder,
        extractor=extractor,
    )

    assert answered.payload.no_answer_reason in {
        NoAnswerReason.NO_RELEVANT_SOURCES,
        NoAnswerReason.SOURCES_DO_NOT_ANSWER,
    }
    assert await verify_chain(session) > 0


# --- what the extractor is handed --------------------------------------------------


async def test_the_extractor_sees_content_only(session, embedder, corpus):
    """It cannot restate provenance it never saw, and cannot be swayed by whose guideline
    it is reading (#18)."""
    extractor = ScriptedExtractor([])

    await answer_query(
        session, question="bisoprolol dose?", actor_id="dr-001", embedder=embedder, extractor=extractor
    )

    [(question, passages)] = extractor.calls
    assert question == "bisoprolol dose?"
    for passage in passages:
        assert "ESC" not in passage
        assert "2021" not in passage


# --- concurrency -------------------------------------------------------------------


class ConcurrencyProbe:
    """Records how many extractions were ever in flight at once."""

    model = "concurrency-probe"

    def __init__(self, *, hold: float = 0.25) -> None:
        self._hold = hold
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def extract(self, question: str, passages: list[str]):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self._hold)  # stands in for model latency
            return ExtractionResult(quotes=[SelectedQuote(source=1, quote=DOSE)])
        finally:
            with self._lock:
                self.active -= 1


async def test_extractions_actually_overlap(engine, embedder, corpus):
    """The test that replaced a piece of theatre.

    The first version fired eight queries and asserted they all finished. They did — and
    they did with the whole pipeline in a single transaction too, so it proved nothing.
    Chasing that turned up the real bug: `extract` is a synchronous call, and awaiting it
    inline blocked the event loop, so nothing could overlap regardless of where the
    transaction boundary sat.

    This measures the thing itself: peak simultaneous extractions. With the call inline,
    that peak is 1.

    What it does *not* prove is the transaction boundary. Committing the read before the
    slow call was justified as protecting the audit's advisory lock; measuring showed the
    lock is taken after extraction and was never held across it. The commit earns its
    place by not pinning a pooled connection — which NullPool in the test fixtures makes
    invisible here. See pipeline.py.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    probe = ConcurrencyProbe()
    concurrency = 6

    async def one(i: int) -> int:
        async with maker() as s:
            answered = await answer_query(
                s,
                question=f"bisoprolol dose, variant {i}?",
                actor_id=f"dr-{i:03d}",
                embedder=embedder,
                extractor=probe,
            )
            return answered.audit_seq

    seqs = await asyncio.gather(*(one(i) for i in range(concurrency)))

    assert probe.peak > 1, (
        f"extractions never overlapped (peak={probe.peak}) — the event loop is blocked, "
        "or the audit lock is being held across the extraction call"
    )
    assert len(set(seqs)) == concurrency, "every query got its own audit row"

    async with maker() as s:
        assert await verify_chain(s) > 0
