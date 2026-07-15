"""Binding a session to a clinic, so row-level security has something to filter on (#31).

**Where the loudness lives, and why it cannot live in the policy.**

The obvious design is a strict policy — `current_setting('app.clinic_id')` with no
`missing_ok` — so a lost tenant context raises instead of quietly returning nothing.
Measured, that does not work, and the way it fails is worse than not working:

- On a connection that has never set the GUC, strict `current_setting` raises. A test
  writes a fresh connection, sees the error, and concludes the system fails loudly.
- After a single `SET LOCAL` earlier on that *same* connection, the parameter is
  registered on the session and afterwards resets to `''` rather than becoming unknown.
  It stops raising.

Under a connection pool — which is every deployment — the first request to set the tenant
disarms the strictness for every later request on that connection. So the test proves a
behaviour production never has. Both `''` and NULL compare as not-true, so both policy
forms fail *closed*, silently, and neither can be made to shout.

That matters here more than it would elsewhere. Zero rows in this system does not read as
"something is broken", it reads as **"the corpus has no guidance on that"** — the exact
confusion `NoAnswerReason` (#20) exists to prevent. A lost tenant context must not be able
to answer a clinical question with silence.

So the guarantee is here instead: a session bound to no clinic raises before it can run
anything. `after_begin` fires on every transaction, including the ones SQLAlchemy opens
after a mid-flow `commit()` — which `pipeline.py` does, and which discards `SET LOCAL`.
Measured: 3 rows before the commit, 0 after. Setting the tenant once in the dependency is
not enough; it has to be re-applied per transaction, and this is the hook that does it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from app.db.session import engine

# The GUC the policies read. One name, one place.
TENANT_SETTING = "app.clinic_id"

# Keys in Session.info.
#
# _SCOPED_KEY marks a session that must have a tenant. It is separate from _TENANT_KEY so
# that "this session forgot its clinic" (an error) and "this session is not tenant-scoped
# at all" (migrations, the CLI, /health) are different states rather than the same absence.
_SCOPED_KEY = "tenant_scoped"
_TENANT_KEY = "clinic_id"


class UnboundTenantSession(RuntimeError):
    """Raised when a tenant-scoped session opens a transaction with no clinic bound.

    This is the loud failure that row-level security cannot provide. Without it a lost
    context is zero rows, and zero rows is a sentence a clinician reads as "no guidance
    exists".
    """


TenantSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# Listens on the Session class, not on this sessionmaker: an async_sessionmaker is a
# factory, not a class, and has no events of its own. The guard below is what keeps that
# from making every session in the process tenant-scoped — a session with no clinic in
# `info` is left alone unless it asked to be scoped.
@event.listens_for(Session, "after_begin")
def _apply_tenant(session: Session, transaction, connection) -> None:
    """Re-apply the tenant at the start of every transaction.

    Not once per request. `SET LOCAL` is transaction-scoped, `pipeline.py` commits
    mid-flow before the slow extraction call, and everything after that commit runs in a
    new transaction with no tenant. Measured before this existed: 3 rows, commit, 0 rows.
    """
    if not session.info.get(_SCOPED_KEY):
        # Not a tenant-scoped session: migrations, the CLI, /health. They connect as roles
        # RLS does not bind, and giving them a tenant would be inventing one.
        return

    clinic = session.info.get(_TENANT_KEY)
    if not clinic:
        raise UnboundTenantSession(
            "a tenant-scoped session opened a transaction with no clinic bound. "
            "Refusing rather than running: with row-level security this query would "
            "return zero rows, and zero rows here reads as 'the corpus has no guidance'."
        )

    # Not a bound parameter: SET LOCAL does not take one. The value is a `sub`-adjacent
    # claim from a signed token, not user input — but "it came from a token" is how
    # injection gets in, so it is validated on the way in rather than trusted here.
    _reject_unsafe(clinic)
    connection.exec_driver_sql(f"SET LOCAL {TENANT_SETTING} = '{clinic}'")


def _reject_unsafe(clinic_id: str) -> None:
    """A clinic id is an opaque identifier, so anything but the opaque-identifier alphabet
    is either a mistake or an attempt.

    SET LOCAL cannot be parameterised, so this string is interpolated into SQL. That is
    the whole reason this function exists: the value arrives inside an RS256-signed token,
    which makes it *authentic*, not *safe*. An issuer misconfigured to copy a user
    attribute into the claim would hand us whatever that attribute says.
    """
    if not clinic_id or len(clinic_id) > 128:
        raise UnboundTenantSession(f"clinic id has an implausible length: {len(clinic_id)}")
    if not all(c.isalnum() or c in "-_." for c in clinic_id):
        raise UnboundTenantSession(
            "clinic id contains characters outside [A-Za-z0-9._-]; it is interpolated "
            "into SET LOCAL, which takes no bound parameters"
        )


def bind_tenant(session: AsyncSession, clinic_id: str) -> None:
    """Attach a clinic to a session before it does anything."""
    _reject_unsafe(clinic_id)
    session.sync_session.info[_SCOPED_KEY] = True
    session.sync_session.info[_TENANT_KEY] = clinic_id


def scope_to_tenants(session: AsyncSession) -> None:
    """Mark a session as tenant-scoped without giving it a clinic yet.

    For the failure this is meant to catch: a session that should be scoped and is not.
    Without it, forgetting `bind_tenant` is indistinguishable from a CLI session and runs
    happily against a policy that returns nothing.
    """
    session.sync_session.info[_SCOPED_KEY] = True


async def tenant_session(clinic_id: str) -> AsyncGenerator[AsyncSession, None]:
    async with TenantSessionLocal() as session:
        bind_tenant(session, clinic_id)
        yield session


async def current_tenant_of(session: AsyncSession) -> str | None:
    """What Postgres thinks the tenant is, read back from the connection.

    For tests and for debugging a leak: `session.info` says what we *meant*, this says
    what the policies will actually filter on.
    """
    return (
        await session.execute(text(f"SELECT current_setting('{TENANT_SETTING}', true)"))
    ).scalar_one()
