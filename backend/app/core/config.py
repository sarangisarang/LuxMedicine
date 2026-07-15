from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://luxmed:luxmed@localhost:5433/luxmedicine"

    # Least-privileged role the API connects as in production. The initial migration
    # revokes UPDATE/DELETE on audit_log from it. Empty => skip (local dev).
    app_db_role: str = ""

    # Must match the vector(N) literal in migration 0001 (EMBEDDING_DIM there).
    # 1024 = multilingual-e5-large, chosen so clinical text can be embedded on our own
    # infrastructure inside the EU rather than sent to a third-party API.
    # Changing this means re-embedding every chunk AND a new migration.
    embedding_dim: int = 1024

    environment: str = "local"

    # Where original PDFs live. Local disk for now; this must become EU-hosted object
    # storage before any real deployment, for the same data-residency reason that
    # decided the embedding model.
    storage_root: Path = Path("./storage")

    # 100 MB. Clinical guidelines run to hundreds of pages, so the limit is generous —
    # it exists to stop an upload sized to exhaust the disk, not to police page count.
    max_upload_bytes: int = 100 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
