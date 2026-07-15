"""Per-clinic chains (#31, migration 0011).

The chains were split because a global chain and per-tenant row-level security cannot
coexist: a tenant who can only see their own rows cannot follow links through rows they
cannot see. That was measured on real Postgres before anything was written — a tenant
restricted to their own rows saw seqs [1, 3, 5] of a five-row chain and reported a break
at seq=3.

These tests are what stops it being reassembled by accident.
"""

import pathlib
import uuid

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog, GENESIS_HASH
from app.models.erasure import LegalBasis
from app.schemas.answer import AnswerPayload, NoAnswerReason
from app.services.audit import (
    ChainBreak,
    _lock_key,
    _row_payload,
    append_audit_entry,
    clinics_with_audit_rows,
    make_query,
    redact_query,
    verify_chain,
    verify_erasure_chain,
)

ALPHA = "clinic-chains-alpha"
BETA = "clinic-chains-beta"


async def ask(session, clinic: str, question: str | None = None):
    question = question or f"a question {uuid.uuid4().hex[:8]}"
    query = make_query(actor_id="dr-001", clinic_id=clinic, text=question, language="en")
    session.add(query)
    await session.flush()

    row = await append_audit_entry(
        session,
        actor_id="dr-001",
        query=query,
        retrieved_chunk_ids=[uuid.uuid4()],
        prompt=f"Question:\n{question}\n\nPassages:\n\n[1]\ntext",
        model="test",
        response=AnswerPayload(no_answer_reason=NoAnswerReason.NO_RELEVANT_SOURCES),
    )
    await session.commit()
    return query, row


# --- the property the split exists to create ---------------------------------------


async def test_a_clinic_can_verify_its_own_chain_without_seeing_another(session):
    """The headline, and the whole reason for 0011.

    The chains are interleaved in seq order, exactly as a global chain would be. Each
    clinic's chain still walks end to end on its own — no gaps, because there are none to
    have. Under a global chain this same interleaving is what made per-tenant
    verification impossible.
    """
    for _ in range(3):
        await ask(session, ALPHA)
        await ask(session, BETA)

    assert await verify_chain(session, clinic_id=ALPHA) >= 3
    assert await verify_chain(session, clinic_id=BETA) >= 3


async def test_each_clinic_starts_at_its_own_genesis(session):
    """Not one chain filtered — separate chains. A clinic's first row points at genesis,
    not at whatever another clinic happened to write first."""
    fresh = f"clinic-{uuid.uuid4().hex[:8]}"
    await ask(session, ALPHA)
    _, row = await ask(session, fresh)

    assert row.prev_hash == GENESIS_HASH


async def test_the_chains_are_interleaved_in_seq_and_still_independent(session):
    """The exact shape that broke a global chain under RLS: alternating rows. seq stays
    globally ordered — nothing depends on it being dense — while prev_hash does not cross
    the boundary."""
    _, a1 = await ask(session, ALPHA)
    _, b1 = await ask(session, BETA)
    _, a2 = await ask(session, ALPHA)

    assert a1.seq < b1.seq < a2.seq, "the rows really are interleaved"
    assert a2.prev_hash == a1.row_hash, "alpha's chain skips over beta's row"
    assert b1.prev_hash != a1.row_hash, "and beta's does not link into alpha's"


async def test_tampering_with_one_clinic_does_not_implicate_another(session):
    """A break is now a statement about one tenant. Under a global chain, any clinic's
    damaged row broke verification for every clinic after it — a tenant could be told
    their trail was untrustworthy because of someone else's incident."""
    from sqlalchemy import text

    await ask(session, ALPHA)
    _, victim = await ask(session, BETA)
    await ask(session, ALPHA)

    await session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_mutate"))
    await session.execute(
        text("UPDATE audit_log SET actor_id = 'forged' WHERE seq = :s"), {"s": victim.seq}
    )
    await session.commit()
    try:
        with pytest.raises(ChainBreak):
            await verify_chain(session, clinic_id=BETA)

        assert await verify_chain(session, clinic_id=ALPHA) >= 2, (
            "alpha's chain is untouched by beta's incident"
        )
    finally:
        await session.execute(
            text("UPDATE audit_log SET actor_id = 'dr-001' WHERE seq = :s"), {"s": victim.seq}
        )
        await session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_mutate"))
        await session.commit()


# --- the clinic is in the hash, not merely in a column ------------------------------


async def test_moving_a_row_to_another_clinic_breaks_the_chain(session):
    """Why clinic_id is in `_row_payload` and not just a column.

    As a bare column, anyone able to write to the table could reassign a row to another
    tenant and every chain would still verify clean — the trail would agree that clinic-b
    asked something clinic-a asked.
    """
    from sqlalchemy import text

    _, row = await ask(session, ALPHA)

    await session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_mutate"))
    await session.execute(
        text("UPDATE audit_log SET clinic_id = :c WHERE seq = :s"), {"c": BETA, "s": row.seq}
    )
    await session.commit()
    try:
        with pytest.raises(ChainBreak):
            await verify_chain(session, clinic_id=BETA)
    finally:
        await session.execute(
            text("UPDATE audit_log SET clinic_id = :c WHERE seq = :s"),
            {"c": ALPHA, "s": row.seq},
        )
        await session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_mutate"))
        await session.commit()


def test_the_hashed_payload_has_exactly_these_keys():
    """A tripwire, not a tautology.

    Adding or removing a key here changes `_canonical`'s bytes for every row already
    written, and all of them stop verifying. 0011 could add `clinic_id` only because the
    corpus was empty — the dev database held zero rows and the test database is rebuilt
    from migrations every session. That window is now shut: with real data, this same
    change needs a per-row payload version and a verifier that switches on it.

    If this test is failing, that is the decision in front of you. It is not a test to
    update.
    """
    row = AuditLog(
        prev_hash="0" * 64,
        actor_id="dr-001",
        clinic_id=ALPHA,
        query_id=uuid.uuid4(),
        query_hash="h",
        retrieved_chunk_ids=[],
        prompt_hash="p",
        model="m",
        response={},
        response_hash="r",
        created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        error=None,
    )

    assert set(_row_payload(row)) == {
        "actor_id",
        "clinic_id",
        "query_id",
        "query_hash",
        "retrieved_chunk_ids",
        "prompt_hash",
        "model",
        "response_hash",
        "created_at",
        "error",
    }


# --- the erasure chain went with it -------------------------------------------------


async def test_the_erasure_chain_is_per_clinic_too(session):
    query_a, _ = await ask(session, ALPHA)
    query_b, _ = await ask(session, BETA)

    for q in (query_a, query_b):
        await redact_query(
            session, q.id, erased_by="dpo-001", legal_basis=LegalBasis.CONSENT_WITHDRAWN
        )
    await session.commit()

    assert await verify_erasure_chain(session, clinic_id=ALPHA) >= 1
    assert await verify_erasure_chain(session, clinic_id=BETA) >= 1


async def test_an_erasure_record_lands_on_the_erased_query_s_clinic(session):
    """Derived from the query, not passed in. A separate argument could disagree, and a
    record of erasure filed under the wrong tenant is a record the right tenant cannot
    find."""
    from app.models.erasure import ErasureLog

    query, _ = await ask(session, BETA)
    await redact_query(
        session, query.id, erased_by="dpo-001", legal_basis=LegalBasis.OBJECTION_UPHELD
    )
    await session.commit()

    record = (
        await session.execute(select(ErasureLog).where(ErasureLog.query_id == query.id))
    ).scalar_one()
    assert record.clinic_id == BETA


# --- the lock ----------------------------------------------------------------------


def test_the_lock_key_is_stable_across_processes():
    """Python's hash() is salted per process, so two uvicorn workers would derive
    different lock keys for the same clinic, never contend, and the lock that stops a
    forked chain (#4) would silently stop working.

    **This test spawns a real subprocess**, because the first version did not. It asserted
    `_lock_key(ALPHA) == _lock_key(ALPHA)` — which `hash()` satisfies perfectly within one
    process, which is the only place it is stable. The mutation run caught it: swapping
    sha256 for `hash()` left every assertion passing. A test named "across processes" that
    never crosses one measures nothing.
    """
    import subprocess
    import sys

    here = _lock_key(ALPHA)

    # A fresh interpreter gets a fresh PYTHONHASHSEED. This is the whole test.
    elsewhere = int(
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.services.audit import _lock_key; "
                f"print(_lock_key({ALPHA!r}))",
            ],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(pathlib.Path(__file__).resolve().parents[1]),
        ).stdout.strip()
    )

    assert here == elsewhere, (
        "two workers derived different lock keys for one clinic: they would never "
        "contend, and the chain would fork with nothing looking wrong"
    )
    assert _lock_key(ALPHA) != _lock_key(BETA)
    assert -(2**31) <= here < 2**31, "must fit pg_advisory_xact_lock's int4"


async def test_two_clinics_do_not_contend_for_the_same_lock(session):
    """The bonus 0011 pays. The old one-argument lock serialised every append in the
    system against every other; two clinics never had a reason to wait on each other."""
    assert _lock_key(ALPHA) != _lock_key(BETA)


# --- system-wide verification is now a loop ----------------------------------------


async def test_every_clinic_with_rows_is_discoverable(session):
    """There is no global chain to walk, so a system-wide check is this list, one clinic
    at a time. An operator's job — it reads across tenants, which is why it lives in the
    CLI and not behind the API."""
    await ask(session, ALPHA)
    await ask(session, BETA)

    clinics = await clinics_with_audit_rows(session)

    assert ALPHA in clinics
    assert BETA in clinics


# --- the verifier must read the table, not its own memory ---------------------------


async def test_the_verifier_is_not_fooled_by_its_own_session_cache(engine):
    """A bug this file found by accident, kept because the fix looks like an optimisation.

    SQLAlchemy's identity map returns an object this session already loaded and does not
    overwrite its attributes from a later SELECT. So a row forged *on disk* was handed
    back with its pre-forgery values, `compute_row_hash` agreed with them, and
    `verify_chain` reported a chain that was intact only in memory. The database said
    'forged'; the ORM said 'dr-001'; the verifier believed the ORM.

    `populate_existing=True` is what makes the one function whose job is to read what is
    actually stored actually read it. Delete it and this test fails.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    # expire_on_commit=False is the condition that exposes it, and it is not exotic: it
    # is what app/api and tests/test_chain_monitor.py already use.
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    clinic = f"clinic-cache-{uuid.uuid4().hex[:6]}"

    async with maker() as s:
        _, row = await ask(s, clinic)
        seq = row.seq

        # The row is now live in this session's identity map.
        assert await verify_chain(s, clinic_id=clinic) == 1

        await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_mutate"))
        await s.execute(
            text("UPDATE audit_log SET actor_id = 'forged' WHERE seq = :s"), {"s": seq}
        )
        await s.commit()
        try:
            on_disk = (
                await s.execute(
                    text("SELECT actor_id FROM audit_log WHERE seq = :s"), {"s": seq}
                )
            ).scalar_one()
            assert on_disk == "forged", "the row really was rewritten in the table"

            with pytest.raises(ChainBreak):
                await verify_chain(s, clinic_id=clinic)
        finally:
            await s.execute(
                text("UPDATE audit_log SET actor_id = 'dr-001' WHERE seq = :s"), {"s": seq}
            )
            await s.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_mutate"))
            await s.commit()
