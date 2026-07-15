from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.services.audit import ChainBreak, verify_chain

settings = get_settings()

app = FastAPI(
    title="LuxMedicine",
    description=(
        "Clinical Search & Retrieval Engine. Returns source-attributed extracts from "
        "clinical guidelines. Does not diagnose, recommend, or advise."
    ),
    version="0.1.0",
)


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "environment": settings.environment}


@app.get("/audit/verify")
async def audit_verify(session: AsyncSession = Depends(get_session)) -> dict:
    """Walk the audit chain end to end.

    Exposed as an endpoint so a clinic's auditor can demand proof on the spot, and so
    the check can be scheduled rather than remembered.
    """
    try:
        verified = await verify_chain(session)
    except ChainBreak as exc:
        return {"intact": False, "broken_at_seq": exc.seq, "reason": exc.reason}
    return {"intact": True, "rows_verified": verified}
