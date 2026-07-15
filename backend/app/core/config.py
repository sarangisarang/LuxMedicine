from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://luxmed:luxmed@localhost:5433/luxmedicine"

    # Least-privileged role the API connects as in production. The initial migration
    # revokes UPDATE/DELETE on audit_log from it. Empty => skip (local dev).
    app_db_role: str = ""

    # Must match the vector(N) column in migration 0001. Changing it later means
    # re-embedding every chunk, so it is fixed at schema-creation time.
    embedding_dim: int = 1536

    environment: str = "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
