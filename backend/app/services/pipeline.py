"""The query flow: retrieve, extract, validate, record (#26).

Every part of this existed already. Nothing connected them, and the roadmap's phrasing —
"wire append_audit_entry *into* the query flow" — assumed a flow to wire it into. This is
that flow, with the audit in it from the start rather than bolted on afterwards.

**No HTTP endpoint here, deliberately.** `actor_id` is a parameter, and the only thing
that may supply it is a verified identity (#30). An endpoint now would have to take the
actor from the request, and an audit trail whose author field the client sets is not
evidence of anything — it is a log again. The pipeline is the work; the endpoint is
twenty lines once identity exists.

**Two things keep the slow call from poisoning everything else, and one of them is not
what it first looked like.**

`extract` is offloaded to a thread. It is a synchronous call that waits seconds on a
network round trip, and awaiting it inline blocks the event loop for every request in
the process. That one is real and the concurrency test catches it: inline, peak
simultaneous extractions is 1.

The read transaction is committed before it. The first guess was that this protects the
audit's advisory lock (#4) from being held across model latency — that guess was wrong,
and measuring it is what showed why: the lock is taken *inside* `append_audit_entry`,
which runs after extraction, so it was never held across the slow call in the first
place. The commit still belongs here, for the duller reason: SQLAlchemy opens a
transaction on the first query, and leaving it open pins a pooled connection for seconds
while doing nothing with it. Under a real pool that is exhaustion, not contention — and
the tests, which use NullPool, cannot see it. Recorded rather than dressed up as
something the suite proves.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.answer import AnswerPayload
from app.services.answering import Extractor, assemble, render_passages
from app.services.audit import append_audit_entry, make_query
from app.services.embedding import Embedder
from app.services.retrieval import SearchHit, hybrid_search
from app.services.table_guard import guard_table_rows
from app.services.validation import RejectedCitation, validate_answer

DEFAULT_LIMIT = 10


@dataclass(frozen=True)
class AnsweredQuery:
    query_id: uuid.UUID
    audit_seq: int
    payload: AnswerPayload

    # What retrieval returned, not what the answer cited. The audit records the same
    # thing: "which sources did the system look at" is the question a dispute asks, and
    # recording only the quoted ones would hide the nine passages it read and discarded.
    hits: list[SearchHit]

    rejected: list[RejectedCitation]
    invalid_sources: int


async def answer_query(
    session: AsyncSession,
    *,
    question: str,
    actor_id: str,
    clinic_id: str,
    embedder: Embedder,
    extractor: Extractor,
    limit: int = DEFAULT_LIMIT,
    include_archived: bool = False,
    language: str | None = None,
) -> AnsweredQuery:
    """Answer one question and record it. Commits.

    `actor_id` must come from a verified identity — see the module docstring. Nothing in
    here checks that, because nothing in here can: it is the caller's job, and #30 is
    what makes a caller capable of it.
    """
    hits = await hybrid_search(
        session, question, embedder, limit=limit, include_archived=include_archived
    )

    # Close the read transaction before the slow call: it pins a pooled connection for
    # the duration otherwise. Not lock-related — see the module docstring on why that
    # first explanation was wrong.
    await session.commit()

    passages = render_passages(hits)
    prompt = _prompt_text(question, passages)

    error: str | None = None
    try:
        # In a thread, not inline. `extract` is synchronous and waits seconds on a
        # network round trip; calling it directly from this coroutine blocks the event
        # loop for every request in the process, not just this one. The protocol stays
        # synchronous so a fake extractor is three lines — the offloading is this
        # module's problem, not the implementer's.
        result = await asyncio.to_thread(extractor.extract, question, passages)
    except Exception as exc:  # noqa: BLE001 — the failure is recorded, then re-raised
        # A failed extraction is still something the clinician asked and did not get an
        # answer to. Recording it is the difference between "the corpus is silent" and
        # "we broke", which is the same distinction NoAnswerReason draws (#20) — and a
        # trail that only contains successes is not a trail.
        error = f"{type(exc).__name__}: {exc}"
        result = None

    answer = assemble(
        question,
        hits,
        result,
        prompt=prompt,
        model=_model_name(extractor),
        query_language=language,
    )

    # #19, before anything is recorded. The audit stores what the clinician was shown,
    # so it must store the validated payload — recording the model's raw output would
    # make the trail evidence of what we caught rather than of what we said.
    validated = validate_answer(answer.payload, {hit.chunk_id: hit.content for hit in hits})

    # #48 safety net, after #19 and never inside it: a verbatim, correctly-cited quote can
    # still be a category-table row shown without its column heading, which reads as the
    # opposite of what it means. Drop those before the audit records what was shown. Not the
    # fix (a structural table extractor is) — this makes #48 fail safe until it lands.
    guarded = guard_table_rows(validated.payload)

    # make_query, not Query(...): the salt and the hash have to be produced together or
    # the row is silently un-erasable. See services/audit.py.
    query = make_query(
        actor_id=actor_id, clinic_id=clinic_id, text=question, language=language
    )
    session.add(query)
    await session.flush()

    row = await append_audit_entry(
        session,
        actor_id=actor_id,
        query=query,
        retrieved_chunk_ids=[hit.chunk_id for hit in hits],
        prompt=prompt,
        model=answer.model,
        response=guarded.payload,
        error=error,
    )
    await session.commit()

    return AnsweredQuery(
        query_id=query.id,
        audit_seq=row.seq,
        payload=guarded.payload,
        hits=hits,
        rejected=validated.rejected,
        invalid_sources=answer.invalid_sources,
    )


def _prompt_text(question: str, passages: list[str]) -> str:
    """The prompt as the extractor renders it.

    Imported lazily: the Claude adapter's module pulls in nothing at import time, but
    keeping the dependency at call scope means a different extractor can supply its own
    rendering without this module knowing which one is in use.
    """
    from app.services.extractor_claude import SYSTEM_PROMPT, render_prompt

    return f"{SYSTEM_PROMPT}\n\n{render_prompt(question, passages)}"


def _model_name(extractor: Extractor) -> str:
    """What the audit records as the model. An extractor that cannot name itself is
    recorded as unknown rather than guessed at — the audit says what happened."""
    return getattr(extractor, "model", "unknown")


def answer_for_display(answered: AnsweredQuery) -> AnswerPayload:
    """The payload, unchanged. Present so the boundary is explicit: what a clinician sees
    and what the audit stored are the same object, and any future divergence has to be
    written here where it can be argued about."""
    return answered.payload
