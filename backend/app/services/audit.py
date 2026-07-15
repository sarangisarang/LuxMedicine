"""Hash-chained audit append and verification.

The chain: row_hash = sha256(prev_hash || canonical(payload)). Editing or removing
any row invalidates every row after it, so tampering is detectable even by someone
with direct database access — which is the whole point, since a log the operator can
silently rewrite is not evidence.

**One chain per clinic** (#31, migration 0011). Not a scaling decision — a global chain
and per-tenant row-level security are incompatible, and that was measured rather than
guessed: a tenant who can only see their own rows sees seqs [1, 3, 5] of a five-row chain
and reports a break at seq=3. Every row they cannot see is a link they cannot follow. A
chain the tenant cannot verify is, from their side, a log with extra steps.

**The payload is frozen now, for real.** Adding any key to `_row_payload` changes
`_canonical`'s bytes for every row already written and fails all of them. 0011 could add
`clinic_id` only because no row existed anywhere. The next such change needs a per-row
payload version; there is a test pinning the key set so nobody discovers this by breaking
production.
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

# Namespace for pg_advisory_xact_lock's two-argument form. The second argument is the
# clinic, so appenders contend only with their own clinic's appenders — which is all the
# chain requires now that each clinic has its own. The one-argument version serialised
# every append in the system against every other, and two clinics never had a reason to
# wait on each other.
_CHAIN_LOCK_NS = 0x4C55584D  # "LUXM"
_ERASURE_LOCK_NS = 0x4C555845  # "LUXE"


def _lock_key(clinic_id: str) -> int:
    """A stable int32 for a clinic, for the advisory lock's second argument.

    Python's hash() is salted per process, so two workers would derive different keys for
    the same clinic and never contend — the lock would silently stop working. sha256 is
    stable across processes, machines and restarts, which is the only property needed
    here. Collisions between clinics cost a little contention and nothing else: the lock
    is an optimisation over the row_hash unique constraint, which remains the guarantee.
    """
    digest = hashlib.sha256(clinic_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big", signed=True)


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


def make_query(*, actor_id: str, clinic_id: str, text: str, language: str | None = None) -> Query:
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
        clinic_id=clinic_id,
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
        # In the payload, not merely a column: without it, someone who can write to the
        # table could move a row to another clinic and the chain would still verify.
        "clinic_id": row.clinic_id,
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

    # Serialises appenders *within a clinic*. Without it, two concurrent requests both
    # read the same tail and write rows claiming the same prev_hash — a forked chain that
    # verifies as tampered. The row_hash unique constraint is the backstop, not the
    # mechanism. Scoped to the clinic because the chain is: clinic-b's appends cannot
    # fork clinic-a's chain, so making them wait for it was pure lost throughput.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :clinic)"),
        {"ns": _CHAIN_LOCK_NS, "clinic": _lock_key(query.clinic_id)},
    )

    tail = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.clinic_id == query.clinic_id)
            .order_by(AuditLog.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    # Each clinic starts at genesis. seq stays globally sequential (it is a bigserial and
    # nothing depends on it being dense); the *chain* is what is per-clinic.
    prev_hash = tail.row_hash if tail else GENESIS_HASH

    response_json = response.model_dump(mode="json")

    row = AuditLog(
        prev_hash=prev_hash,
        actor_id=actor_id,
        # From the query, never a separate argument. Two arguments that must agree
        # eventually will not, and a mismatch here puts a row on the wrong clinic's chain.
        clinic_id=query.clinic_id,
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


async def clinics_with_audit_rows(session: AsyncSession) -> list[str]:
    """Every clinic that has a chain. There is no global chain to walk any more, so a
    system-wide verification is this list, one clinic at a time."""
    return list(
        (await session.execute(select(AuditLog.clinic_id).distinct())).scalars().all()
    )


async def verify_chain(session: AsyncSession, *, clinic_id: str, start_seq: int = 0) -> int:
    """Walk one clinic's chain and confirm every link. Returns rows verified.

    `clinic_id` is required rather than optional-with-a-default. A default would make
    "verify everything" the easy call and "verify this tenant" the deliberate one, and it
    is the tenant's chain that has to be verifiable — by them, with no sight of anyone
    else's rows. That is the whole reason the chains were split (0011).

    Run it on a schedule (#29) — a chain nobody checks proves nothing.
    """
    rows = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.clinic_id == clinic_id, AuditLog.seq > start_seq)
            .order_by(AuditLog.seq)
            # Read the table, not the session. Without this the identity map hands back
            # rows this session loaded earlier and SQLAlchemy leaves their attributes
            # alone — so the verifier hashes what it remembers instead of what is stored,
            # and a row forged on disk verifies clean. Found by a test that forged a row
            # and was told the chain was intact: the DB said 'forged', the ORM said
            # 'dr-001', and compute_row_hash agreed with the ORM.
            #
            # The one function whose entire job is to read what is actually on disk must
            # not be served from a cache.
            .execution_options(populate_existing=True)
        )
    ).scalars().all()

    expected_prev = GENESIS_HASH
    if start_seq > 0:
        anchor = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.clinic_id == clinic_id, AuditLog.seq == start_seq)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if anchor is None:
            raise ChainBreak(start_seq, "start_seq does not exist in this clinic's chain")
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
        "clinic_id": row.clinic_id,
        "erased_by": row.erased_by,
        "legal_basis": row.legal_basis,
        "erased_at": row.erased_at.astimezone(UTC).isoformat(),
    }


def compute_erasure_hash(row: ErasureLog) -> str:
    return sha256_text(row.prev_hash + _canonical(_erasure_payload(row)))


async def verify_erasure_chain(session: AsyncSession, *, clinic_id: str) -> int:
    rows = (
        (
            await session.execute(
                select(ErasureLog)
                .where(ErasureLog.clinic_id == clinic_id)
                .order_by(ErasureLog.seq)
                # See verify_chain: a verifier served from its own session's cache
                # verifies its own memory.
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
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

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :clinic)"),
        {"ns": _ERASURE_LOCK_NS, "clinic": _lock_key(query.clinic_id)},
    )
    tail = (
        await session.execute(
            select(ErasureLog)
            .where(ErasureLog.clinic_id == query.clinic_id)
            .order_by(ErasureLog.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    record = ErasureLog(
        prev_hash=tail.row_hash if tail else ERASURE_GENESIS_HASH,
        clinic_id=query.clinic_id,
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
