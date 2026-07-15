"""Audit export tests (#27).

The export is the document a clinic hands to a lawyer. Everything here is about what it
refuses to hide: a broken chain, a corpus that moved, a source it cannot find.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, text

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion, VersionStatus
from app.services.audit import redact_query
from app.models.erasure import LegalBasis
from app.models.query import Query
from app.services.audit_export import export_audit
from app.services.pipeline import answer_query
from tests.test_pipeline import DOSE, MONITORING, ScriptedExtractor, SimpleEmbedder

# This module's own clinic. The suite is additive and shares one database, so two
# modules sharing a clinic would share a chain -- and a chain test passing because
# of another module's rows proves nothing.
CLINIC = "clinic-audit-export"


DIM = get_settings().embedding_dim


@pytest.fixture
def embedder() -> SimpleEmbedder:
    return SimpleEmbedder()


@pytest.fixture
async def corpus(session, embedder):
    document = Document(
        title=f"Export Guideline {uuid.uuid4().hex[:6]}", issuing_org="ESC", region="EU"
    )
    session.add(document)
    await session.flush()

    marker = f"{document.id}-export"
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
    return version


async def delete_chunk_bypassing_the_guard(session, chunk_id: uuid.UUID) -> None:
    """Delete a cited chunk the only way it can now happen: by dropping #40's trigger.

    That is not a test convenience — it is the scenario the export's reporting exists
    for. #40 makes deletion unreachable for anything short of a privilege that can drop
    the trigger, exactly as 0001's append-only triggers can be dropped by a superuser.
    The hash chain sits above those for the same reason this reporting sits above #40:
    a guard that can be removed needs something that notices afterwards.
    """
    await session.execute(text("DROP TRIGGER chunks_no_delete_when_cited ON chunks"))
    try:
        await session.execute(text("DELETE FROM chunks WHERE id = :id"), {"id": chunk_id})
    finally:
        await session.execute(
            text(
                "CREATE TRIGGER chunks_no_delete_when_cited BEFORE DELETE ON chunks "
                "FOR EACH ROW EXECUTE FUNCTION chunk_is_cited_by_audit()"
            )
        )
    await session.commit()


async def ask(session, embedder, *, actor: str, question: str = "bisoprolol dose?"):
    return await answer_query(
        session,
        question=question,
        actor_id=actor,
        clinic_id=CLINIC,
        embedder=embedder,
        extractor=ScriptedExtractor([DOSE]),
    )


# --- reconstruction ----------------------------------------------------------------


async def test_an_entry_reconstructs_who_asked_what_when_and_against_what(
    session, embedder, corpus
):
    """The question a dispute asks, answered from one object."""
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    answered = await ask(session, embedder, actor=actor, question="bisoprolol dose?")

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    [entry] = export.entries
    assert entry.actor_id == actor
    assert entry.question == "bisoprolol dose?"
    assert entry.recorded_at is not None
    assert entry.seq == answered.audit_seq

    source = next(s for s in entry.sources if DOSE in s.content)
    assert source.issuing_org == "ESC"
    assert source.version_label == "2021"
    assert source.page_start == 45
    assert source.storage_uri, "an expert must be able to open the original and check us"


async def test_the_export_shows_everything_read_not_only_what_was_quoted(session, embedder, corpus):
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    answered = await ask(session, embedder, actor=actor)

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    [entry] = export.entries
    assert len(entry.sources) == len(answered.hits)
    quoted = {c["chunk_id"] for g in entry.response["groups"] for c in g["citations"]}
    assert len(quoted) < len(entry.sources), "more was read than was quoted"


async def test_filters_narrow_to_one_actor_and_window(session, embedder, corpus):
    mine = f"dr-{uuid.uuid4().hex[:6]}"
    theirs = f"dr-{uuid.uuid4().hex[:6]}"
    await ask(session, embedder, actor=mine)
    await ask(session, embedder, actor=theirs)

    export = await export_audit(session, clinic_id=CLINIC, actor_id=mine)
    assert {e.actor_id for e in export.entries} == {mine}

    future = await export_audit(
        session, clinic_id=CLINIC, actor_id=mine, since=datetime.now(UTC) + timedelta(days=1)
    )
    assert future.entries == []


# --- the GDPR boundary, from the other side ----------------------------------------


async def test_an_erased_question_is_gone_and_the_export_says_so(session, embedder, corpus):
    """This test used to be called `test_an_erased_question_still_proves_what_was_asked`,
    and it asserted `question_hash == sha256(question)` — the vulnerability itself, written
    down as a requirement and guarded by CI.

    Its docstring claimed the hash proved what was asked "without our having kept it".
    That was two claims, and they cannot both hold: a hash anyone can check against a
    candidate is a hash anyone can check against every candidate, and clinical questions
    are enumerable. Measured, the old design gave up 20 of 20 questions in 0.3 ms each
    (see tests/test_erasure.py). So the hash *was* keeping it.

    What survives now is the trail, not the question.
    """
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    question = "45yo male, reduced EF — target bisoprolol dose?"
    answered = await ask(session, embedder, actor=actor, question=question)

    await redact_query(
        session, answered.query_id, erased_by="dpo-001", legal_basis=LegalBasis.CONSENT_WITHDRAWN
    )
    await session.commit()

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    [entry] = export.entries
    assert entry.question is None
    assert entry.question_is_erased
    assert entry.redacted_at is not None

    # The hash is still there — audit_log is append-only and hashes it — and it is now
    # worth nothing to anyone, which is the point rather than a regression.
    assert entry.question_hash is not None
    assert not entry.question_hash_is_verifiable
    assert entry.question_hash != hashlib.sha256(question.encode()).hexdigest(), (
        "an unsalted hash of a guessable question is the question"
    )
    assert "unverifiable" in entry.what_the_hash_is_worth

    assert export.chain_intact, "erasure must not cost us the trail"


async def test_before_erasure_the_hash_is_verifiable_by_someone_holding_the_question(
    session, embedder, corpus
):
    """The capability that remains while the query is live: a party presenting a question
    in evidence can confirm it was this one. Erasure is what removes it, deliberately."""
    from app.services.audit import salted_hash

    actor = f"dr-{uuid.uuid4().hex[:6]}"
    question = "45yo male, reduced EF — target bisoprolol dose?"
    answered = await ask(session, embedder, actor=actor, question=question)

    query = (await session.execute(select(Query).where(Query.id == answered.query_id))).scalar_one()

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)
    [entry] = export.entries

    assert entry.question_hash_is_verifiable
    assert entry.question_hash == salted_hash(query.text_salt, question)


# --- what the chain cannot see -----------------------------------------------------


async def test_editing_a_guideline_is_detected_even_though_the_chain_stays_intact(
    session, embedder, corpus
):
    """The gap this export exists to close.

    The chain hashes audit rows, never the corpus. Someone with database access can
    rewrite a guideline's text and every verification still passes — the answer we stored
    would then cite a passage that no longer says what we said it said. Re-checking the
    frozen quotes against the live chunks is the only thing that notices.
    """
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    await ask(session, embedder, actor=actor)

    clean = await export_audit(session, clinic_id=CLINIC, actor_id=actor)
    assert clean.is_evidential
    assert all(c.still_matches for e in clean.entries for c in e.quote_integrity)

    # Rewrite the guideline behind the trail's back.
    await session.execute(
        text("UPDATE chunks SET content = :new WHERE content = :old"),
        {"new": "The target dose of bisoprolol is 2.5 mg once daily.", "old": DOSE},
    )
    await session.commit()

    tampered = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    assert tampered.chain_intact, "the chain is untouched — that is exactly the problem"
    assert not tampered.is_evidential
    broken = [c for e in tampered.entries for c in e.quote_integrity if not c.still_matches]
    assert broken and "corpus changed" in broken[0].reason


async def test_a_deleted_source_is_reported_not_omitted(session, embedder, corpus):
    """Dropping a missing source silently would make an incomplete export look complete —
    in the one document whose whole purpose is completeness.

    Since #40 this needs the trigger bypassed to reach at all. It stays because a guard
    that a superuser can drop needs something that notices afterwards — the same
    relationship the hash chain has with 0001's triggers.

    Note which flag catches it. This deletes a passage the system *read but did not
    quote*, so every quote still matches and `corpus_matches_the_record` stays true — the
    first draft asserted on that and passed for the wrong reason. Losing a read passage
    and losing a quoted one are different failures; `sources_complete` is the one that
    sees this.
    """
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    answered = await ask(session, embedder, actor=actor)

    doomed = next(h.chunk_id for h in answered.hits if DOSE not in h.content)
    await delete_chunk_bypassing_the_guard(session, doomed)

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    [entry] = export.entries
    assert doomed in entry.unresolvable_chunk_ids
    assert doomed not in {s.chunk_id for s in entry.sources}

    assert not entry.sources_complete
    assert entry.corpus_matches_the_record, "the quotes are untouched — a different failure"
    assert not export.is_evidential, "an export that cannot produce what was read is not evidence"


async def test_deleting_a_quoted_passage_fails_both_checks(session, embedder, corpus):
    """The worse case: the passage an answer cited is gone, so the export can neither
    produce it nor confirm we quoted it faithfully."""
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    answered = await ask(session, embedder, actor=actor)

    quoted = next(h.chunk_id for h in answered.hits if DOSE in h.content)
    await delete_chunk_bypassing_the_guard(session, quoted)

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    [entry] = export.entries
    assert not entry.sources_complete
    assert not entry.corpus_matches_the_record
    assert "no longer exists" in next(
        c.reason for c in entry.quote_integrity if not c.still_matches
    )


# --- the chain ---------------------------------------------------------------------


async def test_the_export_carries_the_chain_result_with_the_entries(session, embedder, corpus):
    """Beside the entries, not in a footnote. An export drawn from a tampered trail is
    worse than none — it carries our formatting and our authority."""
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    await ask(session, embedder, actor=actor)

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    assert export.chain_intact
    assert export.chain_break is None
    assert export.is_evidential

    [entry] = export.entries
    assert len(entry.row_hash) == 64 and len(entry.prev_hash) == 64


async def test_a_failed_query_appears_with_its_error(session, embedder, corpus):
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    await answer_query(
        session,
        question="bisoprolol dose?",
        actor_id=actor,
        clinic_id=CLINIC,
        embedder=embedder,
        extractor=ScriptedExtractor(raises=RuntimeError("the model API fell over")),
    )

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    [entry] = export.entries
    assert entry.error is not None and "fell over" in entry.error
    assert entry.sources, "what it read is recorded even though it could not answer"


async def test_an_actor_with_no_history_exports_nothing_and_says_the_chain_is_fine(
    session, embedder, corpus
):
    export = await export_audit(session, clinic_id=CLINIC, actor_id="dr-who-never-asked")

    assert export.entries == []
    assert export.chain_intact
    assert export.is_evidential


# --- ordering ----------------------------------------------------------------------


async def test_entries_are_ordered_by_the_chain_not_by_clock(session, embedder, corpus):
    """seq is the chain's own order. Sorting by timestamp would let clock skew reorder
    evidence."""
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    for i in range(3):
        await ask(session, embedder, actor=actor, question=f"bisoprolol dose, question {i}?")

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)

    seqs = [e.seq for e in export.entries]
    assert seqs == sorted(seqs)
    assert len(seqs) == 3


async def test_the_stored_response_is_returned_verbatim(session, embedder, corpus):
    """What the export shows and what the chain hashed must be the same bytes — anything
    else is a rendering of evidence rather than the evidence."""
    actor = f"dr-{uuid.uuid4().hex[:6]}"
    answered = await ask(session, embedder, actor=actor)

    export = await export_audit(session, clinic_id=CLINIC, actor_id=actor)
    [entry] = export.entries

    from app.models.audit import AuditLog

    row = (
        await session.execute(select(AuditLog).where(AuditLog.seq == answered.audit_seq))
    ).scalar_one()
    assert entry.response == row.response
