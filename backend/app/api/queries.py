"""The query endpoint (#30, completing #26).

The twenty lines `services/pipeline.py` said this would be once identity existed. The
pipeline was finished before this; what was missing was a caller allowed to say who is
asking.

**`QueryRequest` has no `actor_id` field, and that is the security control.** Not a check
that rejects a client-supplied actor — the *absence* of anywhere to put one. Pydantic
ignores unknown keys, so a body carrying `"actor_id": "dr-someone-else"` parses fine and
the value goes nowhere; the only path to `answer_query(actor_id=...)` is the token. This
is the same move as `schemas/answer.py`, where the way to keep the system from issuing a
clinical recommendation is that no field exists to hold one. A validator can be removed
by someone who does not know why it is there. A field that was never added cannot be
filled in by accident.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Clinician, current_clinician
from app.core.vocabulary import Sector
from app.db.session import get_tenant_session
from app.schemas.answer import AnswerPayload
from app.services.answering import Extractor
from app.services.embedding import Embedder
from app.services.pipeline import DEFAULT_LIMIT, answer_query

router = APIRouter(prefix="/queries", tags=["queries"])


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    # BCP-47-ish, and only a hint: it is recorded on the query and passed through for
    # the answer's language, never used to filter the corpus. A guideline in another
    # language is still a source.
    language: str | None = Field(default=None, max_length=16)

    # Which corpus to search: clinical guidance or law. Client-settable, unlike
    # `clinic_id` and `actor_id` — and the difference is worth being explicit about,
    # because the rule elsewhere in this file is the opposite. Those two are tenancy and
    # identity: a client that picks them picks whose data it sees and whose name the
    # audit records. Sector is neither. It selects which shelf the question is asked of,
    # every user may ask either, and choosing wrong returns nothing rather than
    # something it should not have. Pydantic rejects any value outside the enum, so the
    # field cannot become a way to reach an unfiltered search.
    sector: Sector = Sector.MEDICAL

    include_archived: bool = False
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=50)

    # There is no actor_id here. See the module docstring — its absence is the control.


class QueryResponse(BaseModel):
    query_id: uuid.UUID
    audit_seq: int

    # Whose question this was recorded as. Echoed back from the *token*, so a client can
    # see what was written to the trail under their name rather than assume it.
    actor_id: str

    answer: AnswerPayload


def get_embedder() -> Embedder:  # pragma: no cover - overridden in tests and at startup
    raise NotImplementedError(
        "no embedder is wired up: install the `embeddings` extra and override this"
    )


def get_extractor() -> Extractor:  # pragma: no cover - overridden in tests and at startup
    raise NotImplementedError(
        "no extractor is wired up: install the `llm` extra and override this"
    )


@router.post("", response_model=QueryResponse)
async def post_query(
    request: QueryRequest,
    clinician: Clinician = Depends(current_clinician),
    session: AsyncSession = Depends(get_tenant_session),
    embedder: Embedder = Depends(get_embedder),
    extractor: Extractor = Depends(get_extractor),
) -> QueryResponse:
    """Ask the corpus. Recorded against the token's subject.

    Every answer is a hash-chained audit row before it is a response body (#26). There is
    no unaudited way to query, which is the point of putting the audit inside the pipeline
    rather than in this handler: a second endpoint added later cannot forget to call it.
    """
    answered = await answer_query(
        session,
        question=request.question,
        # The token's `sub`, and nothing else in this process can produce this argument.
        actor_id=clinician.actor_id,
        # Likewise from the token. This is the value row-level security filters on, so
        # the request body must not be able to reach it (#31).
        clinic_id=clinician.clinic_id,
        embedder=embedder,
        extractor=extractor,
        sector=request.sector,
        limit=request.limit,
        include_archived=request.include_archived,
        language=request.language,
    )

    return QueryResponse(
        query_id=answered.query_id,
        audit_seq=answered.audit_seq,
        actor_id=clinician.actor_id,
        answer=answered.payload,
    )
