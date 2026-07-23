"""The sector-drift check, in both directions.

The checker's whole value is that it fires on a document whose sector no longer matches its
issuing org. A checker that reports "ok" over drift is worse than no checker, because the
green line is read as evidence — the same failure `check_ground_truth` had when it counted
chunks across the whole corpus and reported "0 stale".

Assertions are scoped to the documents each test creates. The database these run against is
shared, so `find_drift() == []` would be a claim about other tests' rows rather than about
the checker.
"""

from __future__ import annotations

import uuid

import pytest

from app.cli.check_sectors import _check, find_drift
from app.core.vocabulary import IssuingOrg
from app.models.document import Document


async def _add(session, *, org: IssuingOrg, sector: str) -> Document:
    document = Document(
        title=f"sector-drift {uuid.uuid4().hex[:8]}", issuing_org=org.value, sector=sector
    )
    session.add(document)
    await session.flush()
    return document


async def _drift_for(session, document: Document) -> list[str]:
    """The expected sectors reported for this document — empty when it is not drifted."""
    return [expected for d, expected in await find_drift(session) if d.id == document.id]


@pytest.mark.asyncio
async def test_derived_sectors_are_not_drift(session):
    """A document whose sector matches its org is not reported."""
    legal = await _add(session, org=IssuingOrg.BUNDESRECHT, sector="legal")
    medical = await _add(session, org=IssuingOrg.CDC, sector="medical")

    assert await _drift_for(session, legal) == []
    assert await _drift_for(session, medical) == []


@pytest.mark.asyncio
async def test_legal_document_in_the_medical_corpus_is_drift(session):
    """The case that happened: a Bundesrecht document sitting in the medical corpus.

    Retrieval returns it for a medical question and is not wrong to — the label says so.
    Nothing else in the system can see the mistake, which is why it has to be checked
    against the org rather than looked for in the answers.
    """
    document = await _add(session, org=IssuingOrg.BUNDESRECHT, sector="medical")

    assert await _drift_for(session, document) == ["legal"]


@pytest.mark.asyncio
async def test_drift_in_the_other_direction_is_caught_too(session):
    """A medical document parked in the legal corpus is equally invisible, and equally drift.

    Asserting only the legal-into-medical direction would let a checker that hardcodes
    "expected == legal" pass — the mistake made once already when a superseded-row
    measurement compared raw labels and under-counted.
    """
    document = await _add(session, org=IssuingOrg.CDC, sector="legal")

    assert await _drift_for(session, document) == ["medical"]


@pytest.mark.asyncio
async def test_reporting_run_exits_nonzero_and_changes_nothing(session):
    """Without --apply it is a guard: it must fail loudly and leave the row alone."""
    document = await _add(session, org=IssuingOrg.BUNDESRECHT, sector="medical")

    assert await _check(session, apply=False) == 1
    assert document.sector == "medical"
    assert await _drift_for(session, document) == ["legal"]


@pytest.mark.asyncio
async def test_apply_rederives_from_the_org(session):
    """--apply recomputes from the org, and the document is then no longer drifted."""
    document = await _add(session, org=IssuingOrg.BUNDESRECHT, sector="medical")

    assert await _check(session, apply=True) == 0
    assert document.sector == "legal"
    assert await _drift_for(session, document) == []
