"""Hash-chained audit append and verification.

The chain: row_hash = sha256(prev_hash || canonical(payload)). Editing or removing
any row invalidates every row after it, so tampering is detectable even by someone
with direct database access — which is the whole point, since a log the operator can
silently rewrite is not evidence.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import GENESIS_HASH, AuditLog
from app.models.query import Query
from app.schemas.answer import AnswerPayload

# Namespaced lock id for pg_advisory_xact_lock. Any constant works; it just has to
# be the same everywhere so all appenders contend on the same lock.
_CHAIN_LOCK_ID = 0x4C55584D  # "LUXM"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        prompt_hash=sha256_text(prompt),
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


async def redact_query(session: AsyncSession, query_id: uuid.UUID) -> Query:
    """GDPR erasure. Clears the question text; leaves the row, its hash, and the
    entire audit chain untouched. Caller commits.
    """
    query = (await session.execute(select(Query).where(Query.id == query_id))).scalar_one()
    if query.redacted_at is None:
        query.text = None
        query.redacted_at = datetime.now(UTC)
    return query
