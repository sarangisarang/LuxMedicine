"""Hash-chained audit append and verification.

The chain: row_hash = sha256(prev_hash || canonical(payload)). Editing or removing
any row invalidates every row after it, so tampering is detectable even by someone
with direct database access — which is the whole point, since a log the operator can
silently rewrite is not evidence.
"""

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import GENESIS_HASH, AuditLog
from app.models.erasure import ERASURE_GENESIS_HASH, ErasureLog, LegalBasis
from app.models.query import Query
from app.schemas.answer import AnswerPayload

# Namespaced lock id for pg_advisory_xact_lock. Any constant works; it just has to
# be the same everywhere so all appenders contend on the same lock.
_CHAIN_LOCK_ID = 0x4C55584D  # "LUXM"
# A separate lock: the erasure chain has its own tail, and sharing a lock would make
# every erasure contend with every query for no reason.
_ERASURE_LOCK_ID = 0x4C555845  # "LUXE"


def sha256_text(value: str) -> str:
    """Plain sha256. For content whose input space is not enumerable — file bytes, the
    chain's own links, canonical payloads. NOT for a clinician's question: see
    `salted_hash` and migration 0010."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_salt() -> str:
    """32 random bytes, hex. Destroyed on erasure; that destruction is the erasure."""
    return secrets.token_hex(32)


def salted_hash(salt: str, value: str) -> str:
    """sha256(salt || value), for anything a person could guess.

    An unsalted hash of a clinical question is a lookup key for that question: the space
    is drug names x conditions x a handful of phrasings, and 20 of 20 were recovered in
    0.3 ms each on one core. The salt does nothing while the text is still there — an
    attacker with the database has both. Its entire job is what happens after the text
    and the salt are gone together: 2^256 of nothing to search.
    """
    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()


def make_query(*, actor_id: str, text: str, language: str | None = None) -> Query:
    """The only way to build a Query. Salt and hash are produced together.

    Constructing one by hand means passing `text_hash` and `text_salt` separately, and
    two arguments that must agree are two arguments that eventually will not — a Query
    whose hash was computed without its salt is silently un-erasable, and nothing would
    ever say so. Same reason `QueryRequest` has no actor_id field: the way to prevent a
    mistake is to remove the place it can happen.
    """
    salt = new_salt()
    return Query(
        actor_id=actor_id,
        text=text,
        text_salt=salt,
        text_hash=salted_hash(salt, text),
        language=language,
    )


def _canonical(payload: dict) -> str:
    """Byte-stable JSON. sort_keys and fixed separators matter: the verifier must
    reproduce the writer's exact bytes years later, on a different machine.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _row_payload(row: AuditLog) -> dict:
    """Every hashed field, in one place. Writer and verifier both go through here so
    they cannot drift apart.
    """
    return {
        "actor_id": row.actor_id,
        "query_id": str(row.query_id),
        "query_hash": row.query_hash,
        "retrieved_chunk_ids": [str(c) for c in row.retrieved_chunk_ids],
        "prompt_hash": row.prompt_hash,
        "model": row.model,
        "response_hash": row.response_hash,
        "created_at": row.created_at.astimezone(UTC).isoformat(),
        "error": row.error,
    }


def compute_row_hash(row: AuditLog) -> str:
    return sha256_text(row.prev_hash + _canonical(_row_payload(row)))


async def append_audit_entry(
    session: AsyncSession,
    *,
    actor_id: str,
    query: Query,
    retrieved_chunk_ids: list[uuid.UUID],
    prompt: str,
    model: str,
    response: AnswerPayload,
    error: str | None = None,
) -> AuditLog:
    """Append one row, extending the chain.

    Caller commits. The advisory lock is transaction-scoped, so it releases on commit
    or rollback either way.
    """
    if query.text_salt is None:
        # Loudly, rather than falling back to an unsalted hash. A salt-less query is
        # either an already-erased one (appending to it would re-create what was
        # destroyed) or a row from before 0010, whose hashes are baked unsalted into the
        # chain and can never be erased. Both are bugs; neither should be papered over
        # with a plain sha256 that looks like it worked.
        raise ValueError(
            f"query {query.id} has no salt: its hashes would be brute-forceable and the "
            "erasure in #28 could not remove them. Build queries with make_query()."
        )

    # Serialises appenders. Without it, two concurrent requests both read the same
    # tail and write rows claiming the same prev_hash — a forked chain that verifies
    # as tampered. The row_hash unique constraint is the backstop, not the mechanism.
    await session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _CHAIN_LOCK_ID})

    tail = (await session.execute(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1))).scalar_one_or_none()
    prev_hash = tail.row_hash if tail else GENESIS_HASH

    response_json = response.model_dump(mode="json")

    row = AuditLog(
        prev_hash=prev_hash,
        actor_id=actor_id,
        query_id=query.id,
        query_hash=query.text_hash,
        retrieved_chunk_ids=retrieved_chunk_ids,
        # Salted with the query's salt, not plain. The prompt embeds the question
        # verbatim, and this row stores retrieved_chunk_ids — so an attacker resolves
        # those chunks, rebuilds the passage block byte-for-byte, and brute-forces the
        # question out of an unsalted prompt_hash. Measured: 20 of 20 recovered, in under
        # a millisecond. Erasing `text` and salting only `query_hash` would have killed
        # one oracle and left this one wide open.
        prompt_hash=salted_hash(query.text_salt, prompt),
        model=model,
        response=response_json,
        response_hash=sha256_text(_canonical(response_json)),
        created_at=datetime.now(UTC),
        error=error,
    )
    row.row_hash = compute_row_hash(row)

    session.add(row)
    await session.flush()
    return row


class ChainBreak(Exception):  # noqa: N818
    """Raised with the first seq that fails verification."""

    def __init__(self, seq: int, reason: str) -> None:
        super().__init__(f"audit chain broken at seq={seq}: {reason}")
        self.seq = seq
        self.reason = reason


async def verify_chain(session: AsyncSession, *, start_seq: int = 0) -> int:
    """Walk the chain and confirm every link. Returns the number of rows verified.

    Run it in CI against a seeded database, and on a schedule in production — a chain
    nobody checks proves nothing.
    """
    rows = (
        await session.execute(select(AuditLog).where(AuditLog.seq > start_seq).order_by(AuditLog.seq))
    ).scalars().all()

    expected_prev = GENESIS_HASH
    if start_seq > 0:
        anchor = (
            await session.execute(select(AuditLog).where(AuditLog.seq == start_seq))
        ).scalar_one_or_none()
        if anchor is None:
            raise ChainBreak(start_seq, "start_seq does not exist")
        expected_prev = anchor.row_hash

    for row in rows:
        if row.prev_hash != expected_prev:
            raise ChainBreak(row.seq, "prev_hash does not match the preceding row — a row was removed or reordered")
        if compute_row_hash(row) != row.row_hash:
            raise ChainBreak(row.seq, "row_hash does not match its contents — the row was modified")
        expected_prev = row.row_hash

    return len(rows)


def _erasure_payload(row: ErasureLog) -> dict:
    return {
        "query_id": str(row.query_id),
        "erased_by": row.erased_by,
        "legal_basis": row.legal_basis,
        "erased_at": row.erased_at.astimezone(UTC).isoformat(),
    }


def compute_erasure_hash(row: ErasureLog) -> str:
    return sha256_text(row.prev_hash + _canonical(_erasure_payload(row)))


async def verify_erasure_chain(session: AsyncSession) -> int:
    rows = (
        (await session.execute(select(ErasureLog).order_by(ErasureLog.seq))).scalars().all()
    )
    expected_prev = ERASURE_GENESIS_HASH
    for row in rows:
        if row.prev_hash != expected_prev:
            raise ChainBreak(row.seq, "prev_hash does not match — an erasure record was removed")
        if compute_erasure_hash(row) != row.row_hash:
            raise ChainBreak(row.seq, "row_hash does not match its contents")
        expected_prev = row.row_hash
    return len(rows)


async def redact_query(
    session: AsyncSession,
    query_id: uuid.UUID,
    *,
    erased_by: str,
    legal_basis: LegalBasis,
) -> Query:
    """GDPR erasure. Destroys the question and its salt; records that it happened.

    Caller commits. Idempotent: erasing twice is not an error and writes one record, not
    two — a retried request must not look like a second erasure of something already
    gone.

    **What "erased" means here, precisely.** `text` and `text_salt` are nulled together.
    `audit_log` is untouched — it must be, it is append-only and hash-chained — so
    `query_hash` and `prompt_hash` survive as bytes. Without the salt neither can be
    checked against a candidate, by us or by anyone. Before this, both were unsalted and
    both were working oracles: 20 of 20 questions recovered from either one, in under a
    millisecond. See migration 0010.

    **The trade this makes.** After erasure nobody can prove which question was asked.
    The clinician cannot, a regulator cannot, we cannot. That is the definition of
    erasure rather than a shortcoming of it — and if a case needs the question preserved,
    the honest instrument is GDPR Article 17(3), refusing the erasure on the record, not
    keeping a hash that quietly answers the question anyway.
    """
    query = (await session.execute(select(Query).where(Query.id == query_id))).scalar_one()

    if query.redacted_at is not None:
        return query

    query.text = None
    # The line that does the work. Nulling `text` alone leaves the hashes recoverable and
    # the whole thing is theatre.
    query.text_salt = None
    query.redacted_at = datetime.now(UTC)

    await session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _ERASURE_LOCK_ID})
    tail = (
        await session.execute(select(ErasureLog).order_by(ErasureLog.seq.desc()).limit(1))
    ).scalar_one_or_none()

    record = ErasureLog(
        prev_hash=tail.row_hash if tail else ERASURE_GENESIS_HASH,
        # Safe to record: uuid4, not derived from the question. This is the recursion the
        # issue warns about, and the answer is that nothing here is a function of the
        # content — not the id, and not `legal_basis`, which is an enum precisely so that
        # no one can type the erased question into a `reason` field.
        query_id=query.id,
        erased_by=erased_by,
        legal_basis=legal_basis.value,
        erased_at=query.redacted_at,
    )
    record.row_hash = compute_erasure_hash(record)
    session.add(record)
    await session.flush()

    return query
