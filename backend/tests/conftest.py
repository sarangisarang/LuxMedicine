"""Fixtures for tests that need a real Postgres.

These tests run against a throwaway `luxmedicine_test` database built by the actual
migration — not by `Base.metadata.create_all()`. That distinction is the point: the
guarantees under test (the append-only triggers) exist only in migration 0001, and
`create_all()` would produce a schema-shaped database with none of them, so every
test here would pass while proving nothing.

Note what is missing: there is no truncate-between-tests fixture. `audit_log` rejects
TRUNCATE by design, so the usual reset trick is unavailable — appropriately, since a
log the test suite can wipe is a log production can wipe. Tests are additive instead,
and the database is rebuilt once per session.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEST_DB_NAME = "luxmedicine_test"


async def _recreate_database(url) -> None:
    conn = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database="postgres",
    )
    try:
        # Force-disconnect stragglers from a previous run, or DROP blocks.
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{TEST_DB_NAME}' AND pid <> pg_backend_pid()"
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """A fresh database, migrated with alembic exactly as production would be."""
    base_url = make_url(get_settings().database_url)
    asyncio.run(_recreate_database(base_url))

    test_url = base_url.set(database=TEST_DB_NAME).render_as_string(hide_password=False)

    # Run alembic in a subprocess: its env.py calls asyncio.run(), which explodes if
    # invoked from inside pytest-asyncio's running loop.
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": test_url},
        check=True,
        capture_output=True,
    )
    return test_url


@pytest.fixture
async def engine(test_database_url):
    """Function-scoped, with NullPool.

    pytest-asyncio gives each test its own event loop, and an asyncpg connection is
    bound to the loop that opened it. A session-scoped engine would hand a pooled
    connection from test 1's loop to test 2 and fail with "another operation is in
    progress" — a fixture bug that reads like a database bug. NullPool keeps no
    connections between tests, so there is nothing to leak across loops.
    """
    eng = create_async_engine(test_database_url, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def session(engine):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
