from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import current_clinician
from app.core.config import get_settings

if TYPE_CHECKING:
    from app.core.auth import Clinician

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """An unscoped session. /health and the CLI only.

    Anything that touches a clinic's rows wants `get_tenant_session` — under row-level
    security this one sees nothing, and seeing nothing is indistinguishable from "the
    corpus has no guidance". See app/db/tenancy.py.
    """
    async with SessionLocal() as session:
        yield session


async def get_tenant_session(
    clinician: "Clinician" = Depends(current_clinician),
) -> AsyncGenerator[AsyncSession, None]:
    """A session bound to the caller's clinic, for row-level security to filter on.

    The clinic comes from the token and cannot come from anywhere else — it is the tenant
    boundary, so a client-settable one would be a client-settable boundary. Depending on
    `current_clinician` here rather than taking a string means no endpoint can construct
    a tenant session without a verified identity.
    """
    from app.db.tenancy import TenantSessionLocal, bind_tenant

    async with TenantSessionLocal() as session:
        bind_tenant(session, clinician.clinic_id)
        yield session
