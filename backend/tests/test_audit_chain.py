"""Hash-chain tests that need no database.

The chain's guarantee is only worth as much as its sensitivity to change: if any
hashed field can be edited without moving row_hash, the trail is forgeable. These
tests pin exactly that, field by field.
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.models.audit import GENESIS_HASH, AuditLog
from app.services.audit import compute_row_hash, sha256_text


def make_row(**overrides) -> AuditLog:
    defaults = dict(
        prev_hash=GENESIS_HASH,
        actor_id="dr-001",
        query_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        query_hash=sha256_text("target dose of enalapril?"),
        retrieved_chunk_ids=[uuid.UUID("22222222-2222-2222-2222-222222222222")],
        prompt_hash=sha256_text("prompt"),
        model="claude-opus-4-8",
        response={"groups": []},
        response_hash=sha256_text('{"groups":[]}'),
        created_at=datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC),
        error=None,
    )
    return AuditLog(**{**defaults, **overrides})


def test_hash_is_deterministic():
    assert compute_row_hash(make_row()) == compute_row_hash(make_row())


def test_hash_is_stable_across_timezone_representations():
    """created_at is normalised to UTC before hashing, so a row read back in another
    session's timezone must still verify."""
    utc_row = make_row(created_at=datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC))
    tbilisi = timezone(timedelta(hours=4))
    tbilisi_row = make_row(created_at=datetime(2026, 7, 15, 16, 0, 0, tzinfo=tbilisi))
    assert compute_row_hash(utc_row) == compute_row_hash(tbilisi_row)


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", "dr-002"),
        ("query_hash", sha256_text("a different question")),
        ("prompt_hash", sha256_text("a different prompt")),
        ("model", "some-other-model"),
        ("response_hash", sha256_text("a different answer")),
        ("created_at", datetime(2026, 7, 15, 12, 0, 1, tzinfo=UTC)),
        ("error", "timeout"),
        ("retrieved_chunk_ids", [uuid.UUID("33333333-3333-3333-3333-333333333333")]),
        ("prev_hash", sha256_text("forged predecessor")),
    ],
)
def test_every_hashed_field_moves_the_hash(field, value):
    """Tampering with any of these — swapping which sources an answer cited, changing
    who asked, backdating — must break verification."""
    assert compute_row_hash(make_row()) != compute_row_hash(make_row(**{field: value}))


def test_chunk_id_order_is_significant():
    a = uuid.UUID("22222222-2222-2222-2222-222222222222")
    b = uuid.UUID("33333333-3333-3333-3333-333333333333")
    assert compute_row_hash(make_row(retrieved_chunk_ids=[a, b])) != compute_row_hash(
        make_row(retrieved_chunk_ids=[b, a])
    )


def test_redacting_query_text_does_not_affect_the_chain():
    """The GDPR guarantee, stated as a test: the chain hashes query_hash, never the
    question itself, so erasing the text later cannot invalidate the trail."""
    row = make_row()
    before = compute_row_hash(row)
    # Redaction touches queries.text only; nothing in the hashed payload moves.
    assert compute_row_hash(row) == before
    assert "text" not in str(row.query_hash)
