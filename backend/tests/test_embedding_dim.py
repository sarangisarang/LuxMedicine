"""Guards the one number that cannot be changed later without re-embedding everything.

The Chunk model builds its Vector column from Settings.embedding_dim, while migration
0001 pins a literal. That split is intentional — a migration must not depend on the
environment it runs in — but it means the two can silently drift apart, and the symptom
would be a runtime dimension-mismatch error at insert time rather than anything visible
at review time. So they are pinned together here.
"""

import importlib.util
from pathlib import Path

from app.core.config import Settings
from app.models.chunk import Chunk

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_initial_schema.py"


def _migration_dim() -> int:
    spec = importlib.util.spec_from_file_location("migration_0001", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EMBEDDING_DIM


def test_settings_default_matches_the_migration_literal():
    """If this fails, someone changed one and not the other. Changing the dimension
    for real means a new migration plus re-embedding — not editing 0001."""
    assert Settings().embedding_dim == _migration_dim()


def test_chunk_column_matches_the_migration_literal():
    assert Chunk.__table__.c.embedding.type.dim == _migration_dim()


def test_dimension_is_1024_for_multilingual_e5_large():
    """Pinned deliberately: 1024 keeps embedding on self-hosted infrastructure inside
    the EU. Moving to a 1536- or 3072-dim model is a data-residency decision before it
    is a retrieval-quality one, so it should not pass review as an incidental diff."""
    assert _migration_dim() == 1024
