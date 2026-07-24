"""Withdrawal by document, not just by org — because one issuing_org holds documents of
different provenance.

`withdraw.py` began org-scoped, which was enough for #49 (every KDIGO version leaves together).
It stopped being enough the day the legal corpus was measured: the official §5-UrhG law texts and
the pirated commercial commentaries ALL carry issuing_org=`Bundesrecht`, so `--org Bundesrecht`
cannot separate the 1,086 public-domain chunks from the 10,222 commercial ones. These tests pin
the per-document selectors that can, and the one property that makes a bulk withdrawal safe to
run against prod: `--dry-run` changes nothing.

Assertions are scoped to each test's own documents — the database is shared and additive (see
conftest), so "count the withdrawn versions" would be a claim about other tests' rows.
"""

from __future__ import annotations

import uuid

import pytest

from app.cli.withdraw import run_withdrawal
from app.core.vocabulary import IssuingOrg
from app.models.document import Document, DocumentVersion, VersionStatus


async def _add(
    session,
    *,
    title: str,
    org: IssuingOrg = IssuingOrg.BUNDESRECHT,
    sector: str = "legal",
    status: VersionStatus = VersionStatus.ACTIVE,
) -> DocumentVersion:
    document = Document(title=title, issuing_org=org.value, sector=sector)
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_label="2026",
        file_hash=(uuid.uuid4().hex + uuid.uuid4().hex),  # 64 hex chars, unique
        storage_uri=f"{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}.pdf",
        status=status,
    )
    session.add(version)
    await session.flush()
    return version


async def _status(session, version: DocumentVersion) -> VersionStatus:
    await session.refresh(version)
    return version.status


@pytest.mark.asyncio
async def test_title_contains_withdraws_the_book_and_leaves_the_law(session):
    """The measured case: a commercial book and an official law share one org. The title
    selector must take the book and only the book."""
    book = await _add(session, title=f"hoai-2013-praxisleitfaden-simmendinger {uuid.uuid4().hex[:6]}")
    law = await _add(session, title=f"HOAI — Honorarordnung {uuid.uuid4().hex[:6]}")

    rc = await run_withdrawal(
        session, title_contains=["praxisleitfaden-simmendinger"], reason="copyright"
    )

    assert rc == 0
    assert await _status(session, book) == VersionStatus.WITHDRAWN
    assert await _status(session, law) == VersionStatus.ACTIVE, (
        "the official law shares org=Bundesrecht with the book and must stay active"
    )


@pytest.mark.asyncio
async def test_org_selector_cannot_separate_provenance(session):
    """Why the title selector had to exist: --org takes the book AND the law together. This
    documents the limitation rather than hiding it — if a future change makes --org somehow
    discriminate, this test should be the thing that fails and forces the story to be retold."""
    org = f"BR-{uuid.uuid4().hex[:8]}"
    book = await _add(session, title="commercial book", org=IssuingOrg.BUNDESRECHT)
    law = await _add(session, title="official law", org=IssuingOrg.BUNDESRECHT)
    # Give both the same *ad-hoc* org value via direct assignment so the assertion is about
    # this test's two rows only.
    for v in (book, law):
        doc = await session.get(Document, v.document_id)
        doc.issuing_org = org
    await session.flush()

    rc = await run_withdrawal(session, org=org, reason="both, unavoidably")

    assert rc == 0
    assert await _status(session, book) == VersionStatus.WITHDRAWN
    assert await _status(session, law) == VersionStatus.WITHDRAWN


@pytest.mark.asyncio
async def test_id_selector_takes_exactly_the_named_document(session):
    a = await _add(session, title=f"doc-a {uuid.uuid4().hex[:6]}")
    b = await _add(session, title=f"doc-b {uuid.uuid4().hex[:6]}")

    rc = await run_withdrawal(session, ids=[str(a.document_id)], reason="just a")

    assert rc == 0
    assert await _status(session, a) == VersionStatus.WITHDRAWN
    assert await _status(session, b) == VersionStatus.ACTIVE


@pytest.mark.asyncio
async def test_underscore_in_a_title_is_literal_not_a_wildcard(session):
    """`Die_neue_HOAI_2013` and `ISO_310002018` have underscores; LIKE treats `_` as a
    single-character wildcard. Without autoescape, `--title-contains "Die_neue"` would also
    match `DieXneue` — a real risk of withdrawing the wrong document. This is the mutation
    guard: drop autoescape in the code and this test fails."""
    real = await _add(session, title=f"Die_neue_HOAI_2013 {uuid.uuid4().hex[:6]}")
    decoy = await _add(session, title=f"DieXneueXHOAIX2013 {uuid.uuid4().hex[:6]}")

    rc = await run_withdrawal(session, title_contains=["Die_neue"], reason="copyright")

    assert rc == 0
    assert await _status(session, real) == VersionStatus.WITHDRAWN
    assert await _status(session, decoy) == VersionStatus.ACTIVE, (
        "'_' matched as a wildcard — autoescape is off"
    )


@pytest.mark.asyncio
async def test_dry_run_prints_the_plan_and_changes_nothing(session):
    """The property that makes a 20-document withdrawal safe to run against prod first."""
    book = await _add(session, title=f"dry-run book {uuid.uuid4().hex[:6]}")

    rc = await run_withdrawal(
        session, ids=[str(book.document_id)], reason="copyright", dry_run=True
    )

    assert rc == 0
    assert await _status(session, book) == VersionStatus.ACTIVE, "dry-run must not persist"


@pytest.mark.asyncio
async def test_withdraw_requires_a_reason(session):
    """The reason is the log line; a withdrawal without one is a change with no recorded why."""
    book = await _add(session, title=f"needs-reason {uuid.uuid4().hex[:6]}")

    rc = await run_withdrawal(session, ids=[str(book.document_id)])  # no reason

    assert rc == 2
    assert await _status(session, book) == VersionStatus.ACTIVE


@pytest.mark.asyncio
async def test_restore_is_symmetric_and_needs_no_reason(session):
    """A granted licence is one command. Restore does not require a reason — putting a
    document back is not a decision that needs justifying the way removing it is."""
    book = await _add(
        session, title=f"restore-me {uuid.uuid4().hex[:6]}", status=VersionStatus.WITHDRAWN
    )

    rc = await run_withdrawal(session, ids=[str(book.document_id)], restore=True)

    assert rc == 0
    assert await _status(session, book) == VersionStatus.ACTIVE


@pytest.mark.asyncio
async def test_no_match_changes_nothing_and_reports_it(session):
    """The empty-set guard: reporting success over zero matches is the `answered 15/18`
    failure. A selector that matches nothing must exit non-zero."""
    rc = await run_withdrawal(
        session, title_contains=[f"nothing-has-this-title-{uuid.uuid4().hex}"], reason="x"
    )

    assert rc == 1
