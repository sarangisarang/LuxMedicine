"""The erasure endpoint (#28).

Authorised by the same token as everything else (#30): an erasure attributed to whoever
the client claims performed it is not a record of who performed it.

**No `actor_id` field here either**, for the same reason `QueryRequest` has none. And no
`reason` field: `legal_basis` is an enum, because a free-text reason is precisely where
someone writes "erased the question about the enalapril dose for the patient in bed 4"
and re-creates, in an append-only table, the thing that was just destroyed. The issue
calls that the recursion. The defence is not vigilance; it is that no field accepts prose.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import NoResultFound

from app.core.auth import Clinician, current_clinician
from app.db.session import get_session
from app.models.erasure import LegalBasis
from app.services.audit import redact_query

router = APIRouter(prefix="/queries", tags=["erasure"])


class ErasureRequest(BaseModel):
    legal_basis: LegalBasis

    # Nothing else. Not a reason, not an actor, not a note. See the module docstring.


class ErasureResponse(BaseModel):
    query_id: uuid.UUID
    redacted_at: datetime
    erased_by: str

    # Deliberately plain about what remains, because "erased" invites the assumption that
    # the row is gone. It is not: the audit row stays, hash-chained and unaltered. What
    # is gone is the question, the salt, and with them anyone's ability to work out what
    # was asked — including ours.
    detail: str = (
        "question and salt destroyed; the audit row remains and the chain still verifies. "
        "Which question was asked can no longer be established by anyone."
    )


@router.delete("/{query_id}", response_model=ErasureResponse)
async def erase_query(
    query_id: uuid.UUID,
    request: ErasureRequest,
    clinician: Clinician = Depends(current_clinician),
    session: AsyncSession = Depends(get_session),
) -> ErasureResponse:
    """Erase one question. Idempotent.

    Returns 200 on a query that was already erased rather than 404 or 409: a retried
    request must not be able to make a caller think the erasure failed, and the second
    call writes no second record.
    """
    try:
        query = await redact_query(
            session,
            query_id,
            erased_by=clinician.actor_id,
            legal_basis=request.legal_basis,
        )
    except NoResultFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no such query"
        ) from None

    await session.commit()

    return ErasureResponse(
        query_id=query.id,
        redacted_at=query.redacted_at,
        erased_by=clinician.actor_id,
    )
