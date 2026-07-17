from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import documents, erasure, queries, translation
from app.core.auth import Clinician, current_clinician
from app.core.config import get_settings
from app.db.session import get_session, get_tenant_session
from app.services.chain_monitor import verify_and_checkpoint

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wire the embedder and extractor the query endpoint needs.

    `queries.get_embedder` / `get_extractor` raise NotImplementedError by default and say
    they are "overridden in tests and at startup" — this is that startup. It lives here, not
    at import time, for two reasons: loading `multilingual-e5-large` is slow and must not
    happen just because something imports `app.main` (tests do, and supply their own doubles
    via dependency_overrides, which this never clobbers because lifespan does not run under
    ASGITransport); and building the embedder once, here, means the model is loaded a single
    time for the process rather than per request.
    """
    from app.services.embedding import E5Embedder
    from app.services.extractor_gemini import GeminiExtractor
    from app.services.translation import GeminiTranslator

    embedder = E5Embedder()
    extractor = GeminiExtractor()
    translator = GeminiTranslator()
    app.dependency_overrides[queries.get_embedder] = lambda: embedder
    app.dependency_overrides[queries.get_extractor] = lambda: extractor
    app.dependency_overrides[translation.get_translator] = lambda: translator
    try:
        yield
    finally:
        app.dependency_overrides.pop(queries.get_embedder, None)
        app.dependency_overrides.pop(queries.get_extractor, None)
        app.dependency_overrides.pop(translation.get_translator, None)


app = FastAPI(
    title="LuxMedicine",
    description=(
        "Clinical Search & Retrieval Engine. Returns source-attributed extracts from "
        "clinical guidelines. Does not diagnose, recommend, or advise."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(documents.router)
app.include_router(queries.router)
app.include_router(erasure.router)
app.include_router(translation.router)


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "environment": settings.environment}


@app.get(
    "/audit/verify",
    responses={500: {"description": "The chain is broken — the body says where and since when"}},
)
async def audit_verify(
    response: Response,
    clinician: Clinician = Depends(current_clinician),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict:
    """Walk the caller's own chain end to end, and checkpoint it if intact.

    **The caller's clinic, from the token — there is no "verify everything" here.** Chains
    are per clinic (0011), so this is the endpoint a tenant uses to check their own trail,
    which is the only chain they can see and the only one they need. Verifying every
    clinic is an operator's job and lives in `cli/verify_chain.py`, where it belongs: it
    reads across tenants.

    **A break returns 500, not 200.** It used to return 200 with `{"intact": false}` in
    the body, which is the sort of thing that reads fine and fails in production: every
    generic monitor — `curl -f`, an uptime check, a Kubernetes probe, a cron entry that
    checks exit status — treats 200 as healthy. Scheduling that endpoint (#29) would have
    produced a green dashboard over a rewritten trail. A check that reports its own
    failure as success is worse than no check: it manufactures confidence.

    500 rather than a 4xx because a broken chain is not the caller's fault, and rather
    than 503 because retrying will not help.
    """
    result = await verify_and_checkpoint(
        session, clinic_id=clinician.clinic_id, verified_by="http"
    )

    if not result.intact:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {
            "intact": False,
            "broken_at_seq": result.broken_at_seq,
            "reason": result.reason,
            "intact_as_of": (
                result.previous_checkpoint_at.isoformat()
                if result.previous_checkpoint_at
                else None
            ),
            "break_is_bounded": result.break_is_bounded,
        }

    return {
        "intact": True,
        "rows_verified": result.entries_verified,
        "checkpoint_id": result.checkpoint_id,
        "verified_through_seq": result.verified_through_seq,
    }
