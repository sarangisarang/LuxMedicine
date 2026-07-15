"""GDPR erasure (#28).

The headline test is a brute-force attack, run twice. Before the fix it recovers the
"erased" question from the hash the audit keeps; after it, it does not. Anything weaker
would be testing that a column is NULL, which was already true and already meaningless.
"""

import itertools
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.erasure import ErasureLog, LegalBasis
from app.models.query import Query
from app.schemas.answer import AnswerPayload, NoAnswerReason
from app.services.audit import (
    append_audit_entry,
    make_query,
    redact_query,
    salted_hash,
    sha256_text,
    verify_chain,
    verify_erasure_chain,
)

# The attacker's candidate space, built the way an attacker would: this is a clinical
# search engine, so the questions are clinical. Small here to keep the suite fast; the
# point is the *shape* of the space, and the real one is enumerable too.
DRUGS = ["enalapril", "metformin", "amlodipine", "warfarin", "digoxin", "furosemide"]
CONDITIONS = ["CKD stage 3", "heart failure", "hypertension", "pregnancy"]
TEMPLATES = [
    "what is the maximum {d} dose for a patient with {c}?",
    "what is the {d} dose for {c}?",
    "is {d} contraindicated in {c}?",
]
SPACE = [t.format(d=d, c=c) for t, d, c in itertools.product(TEMPLATES, DRUGS, CONDITIONS)]
SECRET = "what is the maximum enalapril dose for a patient with CKD stage 3?"
assert SECRET in SPACE, "the demo must attack a question the attacker could actually guess"


def brute_force_unsalted(target_hash: str) -> str | None:
    """What anyone with the database and a wordlist does."""
    for candidate in SPACE:
        if sha256_text(candidate) == target_hash:
            return candidate
    return None


def brute_force_salted(target_hash: str, salt: str | None) -> str | None:
    if salt is None:
        # The salt is gone. There is nothing to search with, which is the entire point.
        return None
    for candidate in SPACE:
        if salted_hash(salt, candidate) == target_hash:
            return candidate
    return None


async def ask(session: AsyncSession, question: str = SECRET) -> tuple[Query, int]:
    query = make_query(actor_id="dr-001", text=question, language="en")
    session.add(query)
    await session.flush()

    row = await append_audit_entry(
        session,
        actor_id="dr-001",
        query=query,
        retrieved_chunk_ids=[uuid.uuid4()],
        prompt=f"Question:\n{question}\n\nPassages:\n\n[1]\nsome guideline text",
        model="test",
        response=AnswerPayload(no_answer_reason=NoAnswerReason.NO_RELEVANT_SOURCES),
    )
    await session.commit()
    return query, row.seq


# --- the measurement that made this issue urgent -----------------------------------


def test_an_unsalted_hash_is_the_question_it_was_meant_to_erase():
    """The bug, reproduced. Not a test of our code — a test of the claim that the old
    design was broken, kept so the reason for the salt cannot be forgotten and quietly
    optimised away.

    `models/query.py` used to say text_hash survives redaction "without us retaining the
    question". This is what that sentence was worth.
    """
    surviving_hash = sha256_text(SECRET)

    recovered = brute_force_unsalted(surviving_hash)

    assert recovered == SECRET, "a hash of a guessable question is a lookup key for it"


def test_a_salted_hash_survives_the_same_attack_while_the_salt_is_gone():
    salt = "a" * 64
    surviving_hash = salted_hash(salt, SECRET)

    assert brute_force_salted(surviving_hash, salt) == SECRET, "checkable while salted"
    assert brute_force_salted(surviving_hash, None) is None, "and not, once the salt is destroyed"


# --- erasure, end to end -----------------------------------------------------------


async def test_the_erased_question_cannot_be_recovered_from_the_audit(session):
    """The headline.

    After erasure the audit row still holds query_hash and prompt_hash — it must, it is
    append-only and hash-chained. Neither may be usable to work out what was asked.
    """
    query, _ = await ask(session)
    query_id = query.id

    # Before: with the salt in the row, the question is confirmable by anyone with it.
    assert brute_force_salted(query.text_hash, query.text_salt) == SECRET

    await redact_query(
        session, query_id, erased_by="dpo-001", legal_basis=LegalBasis.CONSENT_WITHDRAWN
    )
    await session.commit()

    erased = (await session.execute(select(Query).where(Query.id == query_id))).scalar_one()
    assert erased.text is None
    assert erased.text_salt is None, "nulling the text alone leaves the hash a working oracle"
    assert erased.text_hash is not None, "the bytes stay; audit_log hashes them"

    assert brute_force_salted(erased.text_hash, erased.text_salt) is None
    assert brute_force_unsalted(erased.text_hash) is None, "and it is not secretly unsalted"


async def test_the_prompt_hash_is_not_a_second_way_in(session):
    """The oracle that salting query_hash alone would have left open.

    The prompt embeds the question verbatim, and the audit row names the chunks that were
    in it — so the passage block is reconstructible and only the question is unknown.
    """
    query, seq = await ask(session)
    query_id = query.id

    await redact_query(
        session, query_id, erased_by="dpo-001", legal_basis=LegalBasis.CONSENT_WITHDRAWN
    )
    await session.commit()

    from app.models.audit import AuditLog

    row = (await session.execute(select(AuditLog).where(AuditLog.seq == seq))).scalar_one()

    # The attacker knows the passages (retrieved_chunk_ids says which) and the template.
    for candidate in SPACE:
        rebuilt = f"Question:\n{candidate}\n\nPassages:\n\n[1]\nsome guideline text"
        assert sha256_text(rebuilt) != row.prompt_hash, "prompt_hash must not be unsalted"


async def test_erasure_does_not_break_the_chain(session):
    """The constraint every part of this had to respect. audit_log is untouched — the
    salt lives in `queries` precisely so erasure never needs to write to the one table
    that cannot be written to."""
    query, _ = await ask(session)
    before = await verify_chain(session)

    await redact_query(
        session, query.id, erased_by="dpo-001", legal_basis=LegalBasis.OBJECTION_UPHELD
    )
    await session.commit()

    assert await verify_chain(session) == before, "the chain still verifies, row for row"


async def test_a_salt_less_query_is_refused_rather_than_hashed_plainly(session):
    """A pre-0010 row, or an already-erased one. Falling back to sha256 would produce a
    row that looks audited and is permanently un-erasable."""
    query = Query(
        actor_id="dr-001", text="x", text_hash=sha256_text("x"), text_salt=None, language="en"
    )
    session.add(query)
    await session.flush()

    with pytest.raises(ValueError, match="no salt"):
        await append_audit_entry(
            session,
            actor_id="dr-001",
            query=query,
            retrieved_chunk_ids=[],
            prompt="p",
            model="m",
            response=AnswerPayload(no_answer_reason=NoAnswerReason.NO_RELEVANT_SOURCES),
        )
    await session.rollback()


# --- the record of the erasure -----------------------------------------------------


async def test_the_erasure_is_recorded(session):
    query, _ = await ask(session)

    await redact_query(
        session, query.id, erased_by="dpo-001", legal_basis=LegalBasis.CONSENT_WITHDRAWN
    )
    await session.commit()

    record = (
        await session.execute(select(ErasureLog).where(ErasureLog.query_id == query.id))
    ).scalar_one()
    assert record.erased_by == "dpo-001"
    assert record.legal_basis == "consent_withdrawn"


async def test_the_record_of_the_erasure_does_not_contain_what_was_erased(session):
    """The recursion the issue names.

    Every field of the record, concatenated, must not contain the question — and must not
    let anyone recover it either. query_id is a uuid4: random, not a function of the
    content, so it points at the question without being derived from it.
    """
    query, _ = await ask(session)
    query_id = query.id

    await redact_query(
        session, query_id, erased_by="dpo-001", legal_basis=LegalBasis.CONSENT_WITHDRAWN
    )
    await session.commit()

    record = (
        await session.execute(select(ErasureLog).where(ErasureLog.query_id == query_id))
    ).scalar_one()

    written = " ".join(str(getattr(record, c.name)) for c in ErasureLog.__table__.columns)
    for word in ("enalapril", "CKD", "dose", "maximum"):
        assert word not in written, f"the record of the erasure re-created {word!r}"

    # And not by hashing either: no field is a hash of the question.
    for candidate in SPACE:
        assert sha256_text(candidate) not in written


def test_there_is_no_free_text_field_to_leak_into():
    """The defence is structural, not procedural. A `reason` column is where someone
    types the question back in, and a code-review comment does not survive contact with
    an operator in a hurry."""
    from app.api.erasure import ErasureRequest

    assert set(ErasureRequest.model_fields) == {"legal_basis"}

    columns = {c.name for c in ErasureLog.__table__.columns}
    for leaky in ("reason", "note", "notes", "comment", "description", "detail"):
        assert leaky not in columns


def test_the_legal_basis_is_an_enum_not_prose():
    assert LegalBasis.CONSENT_WITHDRAWN.value == "consent_withdrawn"
    with pytest.raises(ValueError):
        LegalBasis("erased the question about the enalapril dose")


async def test_the_database_refuses_an_unknown_legal_basis(session):
    """The CHECK constraint, not just the enum. The enum guards the app; the constraint
    guards the table from anything that is not the app."""
    with pytest.raises(DBAPIError):
        await session.execute(
            text(
                "INSERT INTO erasure_log (prev_hash, row_hash, query_id, erased_by, "
                "legal_basis, erased_at) VALUES ('0', '1', gen_random_uuid(), 'x', "
                "'because I felt like it', now())"
            )
        )
    await session.rollback()


# --- the erasure record's own integrity --------------------------------------------


async def test_erasure_records_cannot_be_edited_or_deleted(session):
    """An erasure record an operator can remove makes "we erased it" and "we said we
    erased it" indistinguishable — which is the exact pair a regulator is asking to tell
    apart."""
    query, _ = await ask(session)
    await redact_query(
        session, query.id, erased_by="dpo-001", legal_basis=LegalBasis.CONSENT_WITHDRAWN
    )
    await session.commit()

    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("UPDATE erasure_log SET erased_by = 'someone-else'"))
    assert "append-only" in str(exc.value)
    await session.rollback()

    with pytest.raises(DBAPIError):
        await session.execute(text("DELETE FROM erasure_log"))
    await session.rollback()


async def test_erasure_records_cannot_be_truncated(session):
    """Fourth table to need its own guard. Row triggers do not fire on TRUNCATE."""
    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("TRUNCATE erasure_log"))
    assert "append-only" in str(exc.value)
    await session.rollback()


async def test_the_erasure_chain_verifies(session):
    query_a, _ = await ask(session, "what is the enalapril dose for CKD stage 3?")
    query_b, _ = await ask(session, "is warfarin contraindicated in pregnancy?")

    for q in (query_a, query_b):
        await redact_query(
            session, q.id, erased_by="dpo-001", legal_basis=LegalBasis.NO_LONGER_NECESSARY
        )
    await session.commit()

    assert await verify_erasure_chain(session) >= 2


# --- idempotence -------------------------------------------------------------------


async def test_erasing_twice_writes_one_record(session):
    """A retried request must not read as a second erasure of something already gone."""
    query, _ = await ask(session)
    query_id = query.id

    for _ in range(2):
        await redact_query(
            session, query_id, erased_by="dpo-001", legal_basis=LegalBasis.CONSENT_WITHDRAWN
        )
        await session.commit()

    count = (
        await session.execute(
            select(func.count()).select_from(ErasureLog).where(ErasureLog.query_id == query_id)
        )
    ).scalar_one()
    assert count == 1


async def test_erasing_twice_does_not_move_the_timestamp(session):
    """The first erasure is when it happened. Overwriting it would misdate the record."""
    query, _ = await ask(session)
    query_id = query.id

    await redact_query(
        session, query_id, erased_by="dpo-001", legal_basis=LegalBasis.CONSENT_WITHDRAWN
    )
    await session.commit()
    first = (
        await session.execute(select(Query).where(Query.id == query_id))
    ).scalar_one().redacted_at

    await redact_query(
        session, query_id, erased_by="dpo-002", legal_basis=LegalBasis.OBJECTION_UPHELD
    )
    await session.commit()

    again = (
        await session.execute(select(Query).where(Query.id == query_id))
    ).scalar_one().redacted_at
    assert again == first


# --- the factory -------------------------------------------------------------------


def test_make_query_produces_a_hash_that_matches_its_salt():
    query = make_query(actor_id="dr-001", text=SECRET)

    assert query.text_salt is not None
    assert query.text_hash == salted_hash(query.text_salt, SECRET)


def test_two_identical_questions_get_different_hashes():
    """A consequence worth naming: identical questions no longer share a hash, so the
    audit cannot group by "same question asked" any more. That capability was the
    brute-force primitive — an index of which hash means which question is exactly what
    an attacker builds."""
    a = make_query(actor_id="dr-001", text=SECRET)
    b = make_query(actor_id="dr-002", text=SECRET)

    assert a.text_hash != b.text_hash
