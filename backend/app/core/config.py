from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
