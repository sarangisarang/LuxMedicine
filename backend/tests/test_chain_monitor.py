"""Scheduled verification and checkpoints (#29).

The headline test is the HTTP status one. `/audit/verify` returned 200 for a broken chain,
and scheduling that would have produced a green dashboard over a rewritten trail — a check
that reports its own failure as success is worse than no check, because it manufactures
confidence.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import Clinician, current_clinician
from app.db.session import get_session
from app.main import app
from app.models.checkpoint import ChainCheckpoint
from app.schemas.answer import AnswerPayload, NoAnswerReason
from app.services.audit import append_audit_entry, make_query
from app.services.chain_monitor import export_checkpoint, latest_checkpoint, verify_and_checkpoint

# This module's own clinic. The suite is additive and shares one database, so two
# modules sharing a clinic would share a chain -- and a chain test passing because
# of another module's rows proves nothing.
CLINIC = "clinic-chain-monitor"




@asynccontextmanager
async def forged(session: AsyncSession, seq: int | None = None):
    """Rewrite a row's actor_id, then put it back.

    Restoring is not tidiness — the suite is additive (audit_log rejects TRUNCATE, so
    nothing resets), and a forgery left behind breaks the chain for every test that runs
    after it. The first draft forged and walked away; four later tests failed reading the
    damage as their own.

    Disabling the trigger is what a tamper actually looks like: 0001's guard stops the
    app, not someone who can ALTER the table. That is why the hash chain exists on top of
    it, and this test is the chain doing its job.
    """
    if seq is None:
        # This clinic's tail. MAX(seq) globally would forge another module's row now that
        # chains are per clinic — and that test would fail somewhere else entirely.
        seq = (
            await session.execute(
                text("SELECT MAX(seq) FROM audit_log WHERE clinic_id = :c"), {"c": CLINIC}
            )
        ).scalar_one()

    original = (
        await session.execute(text("SELECT actor_id FROM audit_log WHERE seq = :seq"), {"seq": seq})
    ).scalar_one()

    await session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_mutate"))
    await session.execute(
        text("UPDATE audit_log SET actor_id = 'forged' WHERE seq = :seq"), {"seq": seq}
    )
    await session.commit()
    try:
        yield seq
    finally:
        await session.execute(
            text("UPDATE audit_log SET actor_id = :actor WHERE seq = :seq"),
            {"actor": original, "seq": seq},
        )
        await session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_mutate"))
        await session.commit()


async def append_one(session: AsyncSession) -> int:
    question = f"a question {uuid.uuid4().hex[:8]}"
    query = make_query(actor_id="dr-001", clinic_id=CLINIC, text=question, language="en")
    session.add(query)
    await session.flush()

    row = await append_audit_entry(
        session,
        actor_id="dr-001",
        query=query,
        retrieved_chunk_ids=[uuid.uuid4()],
        prompt="p",
        model="m",
        response=AnswerPayload(no_answer_reason=NoAnswerReason.NO_RELEVANT_SOURCES),
    )
    await session.commit()
    return row.seq


# --- the bug that made scheduling pointless ----------------------------------------


@pytest.fixture
async def client(engine):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    # /audit/verify now verifies *the caller's* clinic, because chains are per clinic
    # (0011) and there is no global chain left to walk. The identity is faked here rather
    # than the token minted: tests/test_auth.py is where verification itself is tested,
    # and doing it again through every endpoint tests pyjwt twice and this endpoint once.
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[current_clinician] = lambda: Clinician(
        actor_id="dr-001", clinic_id=CLINIC
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def test_an_intact_chain_returns_200(client, session):
    await append_one(session)

    response = await client.get("/audit/verify")

    assert response.status_code == 200
    assert response.json()["intact"] is True


async def test_a_broken_chain_does_not_return_200(client, session):
    """The whole reason #29 was worth doing.

    `curl -f`, an uptime check, a Kubernetes probe, a cron entry reading exit status —
    every one of them treats 200 as healthy and would have reported a rewritten trail as
    fine. The body said `{"intact": false}` and nothing was reading the body.
    """
    await append_one(session)

    async with forged(session):
        response = await client.get("/audit/verify")

    assert response.status_code == 500, "a broken chain reported as 200 is a green dashboard over a lie"
    body = response.json()
    assert body["intact"] is False
    assert body["broken_at_seq"] is not None


# --- checkpoints -------------------------------------------------------------------


async def test_a_successful_verification_records_a_checkpoint(session):
    seq = await append_one(session)

    result = await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")

    assert result.intact
    assert result.checkpoint_id is not None
    assert result.verified_through_seq == seq

    stored = await latest_checkpoint(session, clinic_id=CLINIC)
    assert stored.verified_through_seq == seq
    assert stored.verified_by == "test"


async def test_a_break_records_no_checkpoint(session):
    """A checkpoint asserts the chain was sound. Writing one for a broken chain would
    assert something false, in the one record that must not."""
    await append_one(session)
    await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")
    before = (await session.execute(select(func.count()).select_from(ChainCheckpoint))).scalar_one()

    async with forged(session):
        result = await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")

    assert not result.intact
    after = (await session.execute(select(func.count()).select_from(ChainCheckpoint))).scalar_one()
    assert after == before


async def test_a_checkpoint_turns_detection_into_a_time_bound(session):
    """The point of #29's second half.

    Without a prior checkpoint the honest answer to "when did this happen" is "sometime
    since genesis". With one, the damage is placed after a known moment.
    """
    await append_one(session)
    clean = await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")
    assert clean.intact

    async with forged(session):
        broken = await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")

    assert not broken.intact
    assert broken.break_is_bounded
    assert broken.previous_checkpoint_at is not None
    assert "intact as of" in export_checkpoint(broken)


async def test_an_unbounded_break_says_so(session):
    """No prior checkpoint means every row since genesis is in question — and the export
    line must say that rather than imply a bound it does not have."""
    from app.services.chain_monitor import VerificationResult

    result = VerificationResult(intact=False, broken_at_seq=7, reason="row_hash does not match")

    assert not result.break_is_bounded
    assert "cannot be bounded" in export_checkpoint(result)


# --- the checkpoint's own integrity ------------------------------------------------


async def test_checkpoints_cannot_be_edited_or_deleted(session):
    """A checkpoint an attacker can delete is a bound they can remove — which is the
    whole thing it exists to deny them."""
    await append_one(session)
    result = await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")

    with pytest.raises(DBAPIError) as exc:
        await session.execute(
            text("UPDATE chain_checkpoints SET verified_by = 'forged' WHERE id = :id"),
            {"id": result.checkpoint_id},
        )
    assert "append-only" in str(exc.value)
    await session.rollback()

    with pytest.raises(DBAPIError):
        await session.execute(
            text("DELETE FROM chain_checkpoints WHERE id = :id"), {"id": result.checkpoint_id}
        )
    await session.rollback()


async def test_checkpoints_cannot_be_truncated(session):
    """The third table to need its own truncate guard. Row triggers do not fire."""
    await append_one(session)
    await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")

    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("TRUNCATE chain_checkpoints"))
    assert "append-only" in str(exc.value)
    await session.rollback()


# --- what a checkpoint is worth ----------------------------------------------------


def test_the_export_line_is_copyable(session=None):
    """The line is the evidence; the table is a convenience. If it cannot be pasted into
    a WORM bucket or a log line, the bound stays inside the system it describes."""
    from app.services.chain_monitor import VerificationResult

    result = VerificationResult(
        intact=True,
        entries_verified=42,
        checkpoint_id=1,
        verified_through_seq=42,
        tail_row_hash="a" * 64,
    )
    line = export_checkpoint(result)

    assert "seq=42" in line
    assert "a" * 64 in line
    assert "\n" not in line, "one line, or it is not a log line"


async def test_an_empty_chain_is_intact_and_not_checkpointed(session):
    """Recording one would be a claim about a chain that does not exist."""
    from app.models.audit import AuditLog

    count = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    if count:
        pytest.skip("the additive suite has already written rows; nothing to say here")

    result = await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")
    assert result.intact
    assert result.checkpoint_id is None


# --- verification depth ------------------------------------------------------------


async def test_verification_walks_from_genesis_not_from_the_checkpoint(session):
    """The tempting optimisation, refused.

    Verifying from the last checkpoint forward would be O(recent) instead of O(all) — and
    would trust the checkpoint to vouch for everything below it, which is exactly what
    someone who rewrote both tables would want. Damage to an old row must still surface.
    """
    first = await append_one(session)
    await append_one(session)
    await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")

    async with forged(session, seq=first):
        result = await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")

    assert not result.intact
    assert result.broken_at_seq == first, "an old row was rewritten and the walk found it"


async def test_the_checkpoint_records_when_not_just_what(session):
    await append_one(session)
    before = datetime.now(UTC) - timedelta(seconds=5)

    await verify_and_checkpoint(session, clinic_id=CLINIC, verified_by="test")

    stored = await latest_checkpoint(session, clinic_id=CLINIC)
    assert stored.verified_at >= before
    assert len(stored.tail_row_hash) == 64
