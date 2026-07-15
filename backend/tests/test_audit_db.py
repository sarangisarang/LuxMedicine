"""Integration tests for the guarantees that live in Postgres, not in Python.

tests/test_audit_chain.py proves the hash maths. It never touches a database, so it
cannot prove the part that actually stops tampering: the triggers, the advisory lock,
and the erasure path. Those are here.

Covers roadmap #2–#5.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit import GENESIS_HASH, AuditLog
from app.models.query import Query
from app.schemas.answer import AnswerPayload, NoAnswerReason
from app.services.audit import append_audit_entry, redact_query, sha256_text, verify_chain


async def make_query(session: AsyncSession, question: str = "Target dose of enalapril?") -> Query:
    query = Query(actor_id="dr-001", text=question, text_hash=sha256_text(question), language="en")
    session.add(query)
    await session.flush()
    return query


async def append_one(session: AsyncSession, question: str = "Target dose of enalapril?") -> AuditLog:
    query = await make_query(session, question)
    return await append_audit_entry(
        session,
        actor_id=query.actor_id,
        query=query,
        retrieved_chunk_ids=[uuid.uuid4()],
        prompt="extract dosing statements from the retrieved passages",
        model="claude-opus-4-8",
        response=AnswerPayload(query_language="en", no_answer_reason=NoAnswerReason.NO_RELEVANT_SOURCES),
    )


# --- #2: UPDATE and DELETE ---------------------------------------------------------


async def test_update_on_audit_log_is_rejected(session):
    """Roadmap #2. Rewriting who asked, or which sources an answer cited, is the exact
    attack an audit trail exists to prevent — and it must fail at the database, since
    an ORM-level guard is advisory to anything holding a connection."""
    row = await append_one(session)
    await session.commit()
    # Read seq out now: rollback() expires ORM instances regardless of
    # expire_on_commit, and touching row.seq afterwards would trigger a lazy load.
    seq = row.seq

    with pytest.raises(DBAPIError) as exc:
        await session.execute(
            text("UPDATE audit_log SET actor_id = 'forged' WHERE seq = :seq"), {"seq": seq}
        )
    assert "append-only" in str(exc.value)
    await session.rollback()

    unchanged = (await session.execute(select(AuditLog).where(AuditLog.seq == seq))).scalar_one()
    assert unchanged.actor_id == "dr-001"


async def test_delete_on_audit_log_is_rejected(session):
    """Roadmap #2. Deleting an inconvenient entry must be impossible, not merely
    discouraged."""
    row = await append_one(session)
    await session.commit()
    seq = row.seq  # see the note in the UPDATE test above

    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("DELETE FROM audit_log WHERE seq = :seq"), {"seq": seq})
    assert "append-only" in str(exc.value)
    await session.rollback()

    still_there = (await session.execute(select(AuditLog).where(AuditLog.seq == seq))).scalar_one_or_none()
    assert still_there is not None


# --- #3: TRUNCATE ------------------------------------------------------------------


async def test_truncate_on_audit_log_is_rejected(session):
    """Roadmap #3. TRUNCATE bypasses row-level triggers entirely — this is why
    audit_log_no_truncate is a separate statement-level trigger, and why a suite that
    only tested UPDATE/DELETE would have missed the widest hole in the design."""
    await append_one(session)
    await session.commit()

    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("TRUNCATE audit_log CASCADE"))
    assert "append-only" in str(exc.value)
    await session.rollback()

    remaining = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    assert remaining > 0


# --- #4: concurrency ---------------------------------------------------------------


async def test_concurrent_appends_do_not_fork_the_chain(engine):
    """Roadmap #4. The reason pg_advisory_xact_lock exists.

    Without it, concurrent writers read the same chain tail and each writes a row
    claiming the same prev_hash — a fork that verifies as tampered, produced by
    ordinary traffic rather than by an attacker.

    Asserting 'no crash' would be too weak: the row_hash unique constraint is a
    backstop, not the mechanism. So this asserts the shape of the result — every row
    committed, every prev_hash unique, and the whole chain verifying.
    """
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    concurrency = 12

    async def one(i: int) -> None:
        async with maker() as s:
            await append_one(s, question=f"concurrent question {i}")
            await s.commit()

    async with maker() as s:
        before = (await s.execute(select(func.count()).select_from(AuditLog))).scalar_one()

    await asyncio.gather(*(one(i) for i in range(concurrency)))

    async with maker() as s:
        rows = (await s.execute(select(AuditLog).order_by(AuditLog.seq))).scalars().all()
        assert len(rows) == before + concurrency, "an append was lost under contention"

        prev_hashes = [r.prev_hash for r in rows]
        assert len(set(prev_hashes)) == len(prev_hashes), "two rows share a prev_hash — the chain forked"

        assert await verify_chain(s) == len(rows)


# --- #5: GDPR erasure --------------------------------------------------------------


async def test_redaction_clears_text_and_leaves_the_chain_intact(session):
    """Roadmap #5. The GDPR/immutability contract, end to end.

    If this ever fails, the design is wrong rather than the test: the two requirements
    were reconciled by hashing the question instead of retaining it, and deleting the
    row instead would force an UPDATE on audit_log that the trigger rejects.
    """
    question = "45yo male, reduced EF, already on ACE-inhibitor — target dose?"
    query = await make_query(session, question)
    original_hash = query.text_hash

    await append_audit_entry(
        session,
        actor_id=query.actor_id,
        query=query,
        retrieved_chunk_ids=[uuid.uuid4()],
        prompt="extract",
        model="claude-opus-4-8",
        response=AnswerPayload(query_language="en", no_answer_reason=NoAnswerReason.NO_RELEVANT_SOURCES),
    )
    await session.commit()

    await redact_query(session, query.id)
    await session.commit()

    refreshed = (await session.execute(select(Query).where(Query.id == query.id))).scalar_one()
    assert refreshed.text is None, "the personal data is still there"
    assert refreshed.redacted_at is not None
    assert refreshed.text_hash == original_hash, "the hash must survive: it is what still proves what was asked"

    # The erasure must not have cost us the trail.
    assert await verify_chain(session) > 0


async def test_redaction_is_idempotent(session):
    """A second erasure request must not overwrite the first one's timestamp — the
    record of when we complied is itself worth keeping."""
    query = await make_query(session, "another question")
    await session.commit()

    await redact_query(session, query.id)
    await session.commit()
    first_redacted_at = query.redacted_at

    await redact_query(session, query.id)
    await session.commit()
    assert query.redacted_at == first_redacted_at


# --- chain anchoring ---------------------------------------------------------------


async def test_first_row_anchors_to_genesis(session):
    first = (await session.execute(select(AuditLog).order_by(AuditLog.seq).limit(1))).scalar_one()
    assert first.prev_hash == GENESIS_HASH
