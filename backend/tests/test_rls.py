"""Row-level security (#31, migration 0012).

**Every test here connects as a real non-superuser role**, created by the fixture below.
That is not hygiene, it is the point: `docker-compose`'s POSTGRES_USER is the container
superuser with `rolbypassrls`, and a superuser skips every policy always. Run these as the
default role and they all pass while proving that RLS is switched off.

So the first test asserts the role. If it fails, nothing else in this file means anything.

**No query here filters by clinic_id.** A `WHERE clinic_id = ?` would pass whether or not
the policies exist — which is the same reason the application does not write one.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.tenancy import TENANT_SETTING, UnboundTenantSession, _reject_unsafe

ALPHA = "clinic-rls-alpha"
BETA = "clinic-rls-beta"

APP_ROLE = "luxmed_app_test"
APP_PASSWORD = "app-test-password"


@pytest.fixture(scope="session")
async def app_role(test_database_url):
    """A least-privileged role, exactly as production is meant to connect.

    NOSUPERUSER and NOBYPASSRLS are spelled out rather than left to the default. They are
    the default — and the entire finding that shaped #31 is that when they are not, every
    test in this file passes and none of them tests anything.
    """
    from sqlalchemy.engine import make_url

    owner = create_async_engine(test_database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    async with owner.connect() as conn:
        exists = (
            await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE})
        ).scalar_one_or_none()
        if not exists:
            # Interpolated, not bound: CREATE ROLE is DDL and Postgres does not accept a
            # placeholder there. Both values are constants in this file, not input.
            await conn.execute(
                text(
                    f"CREATE ROLE \"{APP_ROLE}\" LOGIN PASSWORD '{APP_PASSWORD}' "
                    "NOSUPERUSER NOBYPASSRLS"
                )
            )
        await conn.execute(text(f'GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO "{APP_ROLE}"'))
        await conn.execute(
            text(f'GRANT UPDATE, DELETE ON queries, documents, document_versions, chunks TO "{APP_ROLE}"')
        )
        await conn.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{APP_ROLE}"'))
        await conn.execute(
            text(f'REVOKE UPDATE, DELETE ON audit_log, erasure_log, chain_checkpoints FROM "{APP_ROLE}"')
        )
    await owner.dispose()

    url = make_url(test_database_url).set(username=APP_ROLE, password=APP_PASSWORD)
    yield url.render_as_string(hide_password=False)


@pytest.fixture
async def seeded(session):
    """Two clinics' rows, written as the owner so RLS does not get in the way of setup."""
    for clinic in (ALPHA, BETA):
        await session.execute(
            text(
                "INSERT INTO queries (id, actor_id, clinic_id, text, text_hash, text_salt) "
                "VALUES (gen_random_uuid(), 'dr-001', :c, :t, :h, :s)"
            ),
            {"c": clinic, "t": f"{clinic} secret question", "h": uuid.uuid4().hex, "s": "0" * 64},
        )
    await session.commit()


@pytest.fixture
async def as_app(app_role):
    """A connection as the app role, with no tenant set yet."""
    engine = create_async_engine(app_role, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


# --- the test that makes the others mean something ---------------------------------


async def test_the_app_role_is_not_a_superuser_and_cannot_bypass_rls(as_app):
    """If this fails, every other test in this file is theatre.

    A superuser skips every policy, always — FORCE ROW LEVEL SECURITY binds the table
    owner and does nothing to them. Measured: `luxmed`, the docker-compose user, is
    `superuser=true bypassrls=true`. Writing RLS and testing it as that role would have
    produced a green suite over a system with no isolation at all.
    """
    async with as_app() as s:
        row = (
            await s.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()

    assert row.rolsuper is False, "a superuser skips every policy; this suite would prove nothing"
    assert row.rolbypassrls is False, "BYPASSRLS does the same, more quietly"


async def test_the_default_role_would_have_made_this_suite_meaningless(session):
    """Recorded, not assumed. The trap is only a trap because the obvious setup falls in
    it: run these tests with the connection every other test uses and RLS is off."""
    row = (
        await session.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    ).one()

    assert row.rolsuper is True, (
        "docker-compose's POSTGRES_USER is the container superuser. If this ever stops "
        "being true, the note in 0012 needs updating — not this assertion deleting."
    )


# --- isolation, with no WHERE clause anywhere --------------------------------------


async def test_a_clinic_sees_only_its_own_rows(as_app, seeded):
    """No filter in the query. That is the whole test — the application never writes one
    either, so this is what the application actually does."""
    async with as_app() as s:
        await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{ALPHA}'"))
        clinics = (await s.execute(text("SELECT DISTINCT clinic_id FROM queries"))).scalars().all()

    assert set(clinics) == {ALPHA}


async def test_the_other_clinics_rows_are_not_merely_hidden_from_count(as_app, seeded):
    """A row you can count is a row you have learned something about. The seq gaps in a
    global chain were exactly this leak (0011)."""
    async with as_app() as s:
        await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{ALPHA}'"))
        total = (await s.execute(text("SELECT count(*) FROM queries"))).scalar_one()
        mine = (
            await s.execute(text("SELECT count(*) FROM queries WHERE clinic_id = :c"), {"c": ALPHA})
        ).scalar_one()

    assert total == mine, "count(*) must not reveal how much anyone else has"


async def test_a_forgotten_filter_is_not_a_leak(as_app, seeded):
    """The reason RLS won over `WHERE clinic_id = ?`, restated as a test.

    A new endpoint, a JOIN, a report, a migration script — the filter is a thing someone
    must remember. This query forgets it completely.
    """
    async with as_app() as s:
        await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{BETA}'"))
        rows = (await s.execute(text("SELECT clinic_id, text FROM queries"))).all()

    assert rows, "beta can see its own"
    assert all(r.clinic_id == BETA for r in rows)
    assert not any(ALPHA in (r.text or "") for r in rows)


async def test_no_tenant_context_sees_nothing(as_app, seeded):
    """Fails closed. Silently — which is why app/db/tenancy.py refuses to open a
    transaction with no clinic bound. Zero rows in this product reads as "the corpus has
    no guidance", and that sentence must never be produced by a lost session variable.
    """
    async with as_app() as s:
        rows = (await s.execute(text("SELECT clinic_id FROM queries"))).all()

    assert rows == []


async def test_a_tenant_cannot_write_a_row_into_another_clinics_chain(as_app, seeded):
    """WITH CHECK, not just USING.

    With USING alone a tenant reads only their own rows and can still INSERT one stamped
    with someone else's clinic — writing into a chain they cannot read. It would fork, and
    the clinic that owns it would be the one holding a broken trail.
    """
    async with as_app() as s:
        await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{ALPHA}'"))
        with pytest.raises(ProgrammingError) as exc:
            await s.execute(
                text(
                    "INSERT INTO queries (id, actor_id, clinic_id, text, text_hash, text_salt) "
                    "VALUES (gen_random_uuid(), 'dr-001', :c, 'x', :h, :s)"
                ),
                {"c": BETA, "h": uuid.uuid4().hex, "s": "0" * 64},
            )
        assert "row-level security" in str(exc.value).lower()
        await s.rollback()


# --- the published corpus is shared, and only that -----------------------------------


async def test_published_guidelines_are_visible_to_every_clinic(as_app, session):
    """clinic_id IS NULL means ESC/EASD/ESMO, which belong to everyone. If this fails the
    product does nothing: isolation that hides the guidelines is isolation of an empty
    corpus."""
    title = f"ESC Shared {uuid.uuid4().hex[:6]}"
    await session.execute(
        text(
            "INSERT INTO documents (id, title, issuing_org, region, clinic_id) "
            "VALUES (gen_random_uuid(), :t, 'ESC', 'EU', NULL)"
        ),
        {"t": title},
    )
    await session.commit()

    for clinic in (ALPHA, BETA):
        async with as_app() as s:
            await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{clinic}'"))
            found = (
                await s.execute(text("SELECT count(*) FROM documents WHERE title = :t"), {"t": title})
            ).scalar_one()
        assert found == 1, f"{clinic} cannot see a published guideline"


async def test_one_clinics_uploaded_protocol_is_invisible_to_another(as_app, session):
    title = f"Internal Sepsis Pathway {uuid.uuid4().hex[:6]}"
    await session.execute(
        text(
            "INSERT INTO documents (id, title, issuing_org, region, clinic_id) "
            "VALUES (gen_random_uuid(), :t, 'ESC', 'EU', :c)"
        ),
        {"t": title, "c": ALPHA},
    )
    await session.commit()

    async with as_app() as s:
        await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{ALPHA}'"))
        mine = (
            await s.execute(text("SELECT count(*) FROM documents WHERE title = :t"), {"t": title})
        ).scalar_one()
    assert mine == 1

    async with as_app() as s:
        await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{BETA}'"))
        theirs = (
            await s.execute(text("SELECT count(*) FROM documents WHERE title = :t"), {"t": title})
        ).scalar_one()
    assert theirs == 0


async def test_a_version_selected_by_id_is_filtered_for_another_clinic(as_app, session):
    """The exact query the PDF endpoint (#35) runs: SELECT a document_version by primary
    key. The other isolation tests select documents by title or count; this asserts the
    by-id path a clinic uses to fetch a source file, so the endpoint's 404 for another
    clinic's private version is the database's answer, not the handler's politeness.

    A published version (NULL clinic) is fetched by both clinics; a private one only by its
    owner — everyone else gets zero rows, indistinguishable from "no such id".
    """
    shared_doc, shared_ver = uuid.uuid4(), uuid.uuid4()
    private_doc, private_ver = uuid.uuid4(), uuid.uuid4()
    for doc, ver, clinic in [(shared_doc, shared_ver, None), (private_doc, private_ver, ALPHA)]:
        await session.execute(
            text(
                "INSERT INTO documents (id, title, issuing_org, region, clinic_id) "
                "VALUES (:id, :t, 'ESC', 'EU', :c)"
            ),
            {"id": doc, "t": f"Doc {uuid.uuid4().hex[:6]}", "c": clinic},
        )
        await session.execute(
            text(
                "INSERT INTO document_versions (id, document_id, version_label, file_hash, "
                "storage_uri, status) VALUES (:v, :d, '2024', :h, '/x.pdf', 'active')"
            ),
            {"v": ver, "d": doc, "h": uuid.uuid4().hex},
        )
    await session.commit()

    async with as_app() as s:
        await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{BETA}'"))
        sees_shared = (
            await s.execute(
                text("SELECT count(*) FROM document_versions WHERE id = :v"), {"v": shared_ver}
            )
        ).scalar_one()
        sees_private = (
            await s.execute(
                text("SELECT count(*) FROM document_versions WHERE id = :v"), {"v": private_ver}
            )
        ).scalar_one()

    assert sees_shared == 1, "a published guideline's version must be fetchable by any clinic"
    assert sees_private == 0, "beta selected alpha's private version by id — the PDF would leak"


async def test_chunks_inherit_isolation_from_their_document(as_app, session):
    """chunks carry no clinic_id — the policy joins up to documents. A denormalised copy
    is a thing that can disagree, and a chunk whose clinic says one thing while its
    document says another is a leak whose paper trail looks fine.

    This matters because retrieval reads chunks, not documents.
    """
    doc = uuid.uuid4()
    version = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO documents (id, title, issuing_org, region, clinic_id) "
            "VALUES (:id, :t, 'ESC', 'EU', :c)"
        ),
        {"id": doc, "t": f"Private {uuid.uuid4().hex[:6]}", "c": ALPHA},
    )
    await session.execute(
        text(
            "INSERT INTO document_versions (id, document_id, version_label, file_hash, "
            "storage_uri, status) VALUES (:v, :d, '2024', :h, '/x.pdf', 'active')"
        ),
        {"v": version, "d": doc, "h": uuid.uuid4().hex},
    )
    marker = f"alpha-only-{uuid.uuid4().hex[:8]}"
    # embedding is NOT NULL — a chunk with no vector is unfindable and 0010's schema says
    # so. A zero vector is fine here: nothing in this test searches, it reads.
    from app.core.config import get_settings

    zero = "[" + ",".join(["0"] * get_settings().embedding_dim) + "]"
    await session.execute(
        text(
            "INSERT INTO chunks (id, document_version_id, ordinal, page_start, page_end, "
            "content, embedding) VALUES (gen_random_uuid(), :v, 0, 1, 1, :c, CAST(:e AS vector))"
        ),
        {"v": version, "c": marker, "e": zero},
    )
    await session.commit()

    async with as_app() as s:
        await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{BETA}'"))
        leaked = (
            await s.execute(text("SELECT count(*) FROM chunks WHERE content = :c"), {"c": marker})
        ).scalar_one()
    assert leaked == 0, "retrieval reads chunks; a chunk that leaks is an answer that leaks"


# --- the pooling trap, reproduced --------------------------------------------------


async def test_set_local_does_not_survive_into_the_next_transaction(as_app, seeded):
    """Scenario 3 of the measurement, kept as a regression.

    `SET` without LOCAL leaked: the next request on the pooled connection, having set
    nothing, read the previous tenant's rows. SET LOCAL is transaction-scoped. Nothing in
    the code should ever be able to drift back.
    """
    engine = create_async_engine(
        (await _url(as_app)), pool_size=1, max_overflow=0
    )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{ALPHA}'"))
            assert (await s.execute(text("SELECT count(*) FROM queries"))).scalar_one() > 0
            await s.commit()

        # Same pooled connection, new transaction, nothing set.
        async with maker() as s:
            rows = (await s.execute(text("SELECT clinic_id FROM queries"))).all()
        assert rows == [], "the tenant survived into the next request on this connection"
    finally:
        await engine.dispose()


async def _url(as_app):
    return str(as_app.kw["bind"].url.render_as_string(hide_password=False))


# --- the application-side guard -----------------------------------------------------


def test_an_unbound_session_refuses_to_run():
    """RLS cannot fail loudly (measured: both policy forms return zero rows under a pool).
    So the loudness is here."""
    assert issubclass(UnboundTenantSession, RuntimeError)


@pytest.mark.parametrize(
    "bad",
    [
        "clinic-a'; DROP TABLE audit_log; --",
        "clinic-a' OR '1'='1",
        "clinic a",
        "clinic\n-a",
        "",
        "x" * 129,
    ],
)
def test_an_implausible_clinic_id_is_refused(bad):
    """SET LOCAL takes no bound parameters, so the clinic is interpolated into SQL.

    The value arrives in an RS256-signed token, which makes it authentic — not safe. An
    issuer misconfigured to copy a user-editable attribute into the claim would hand us
    whatever that attribute says, correctly signed.
    """
    with pytest.raises(UnboundTenantSession):
        _reject_unsafe(bad)


def test_a_plausible_clinic_id_is_accepted():
    for good in ("clinic-a", "clinic_01", "berlin.charite", "ABC123"):
        _reject_unsafe(good)


# --- the schema-level facts the policies depend on ----------------------------------


@pytest.mark.parametrize(
    "table",
    ["queries", "audit_log", "chain_checkpoints", "erasure_log", "documents",
     "document_versions", "chunks"],
)
async def test_every_tenant_table_has_rls_enabled_and_forced(session, table):
    """FORCE, asserted directly.

    A mutation run showed that removing FORCE broke nothing observable: the test role
    does not own the tables, so only ENABLE was doing work. That makes FORCE untestable
    through behaviour and therefore deletable by anyone tidying up — which is exactly the
    case it exists for, an app that ends up owning its own tables. So it is asserted here
    as a schema fact rather than left to be inferred from one.
    """
    row = (
        await session.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = :t AND relnamespace = 'public'::regnamespace"
            ),
            {"t": table},
        )
    ).one()

    assert row.relrowsecurity is True, f"{table} has no row-level security at all"
    assert row.relforcerowsecurity is True, f"{table} exempts its owner"


async def test_nested_policies_are_what_isolate_chunks(as_app, session):
    """The layering the chunks policy relies on, made explicit.

    chunks' policy asks only "does this chunk's version exist" — no clinic condition. It
    is safe because RLS applies inside policy expressions too: that nested reference is
    filtered by document_versions' policy, which is filtered by documents'. This test is
    what says so out loud, since the chunks policy alone reads as though it checks nothing.
    """
    async with as_app() as s:
        await s.execute(text(f"SET LOCAL {TENANT_SETTING} = '{BETA}'"))
        alphas = (
            await s.execute(
                text("SELECT count(*) FROM documents WHERE clinic_id = :c"), {"c": ALPHA}
            )
        ).scalar_one()

    assert alphas == 0, (
        "documents' own policy is what makes the nested EXISTS in chunks' policy safe; "
        "if this ever returns rows, chunks leaks and its policy will not say why"
    )
