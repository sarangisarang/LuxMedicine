from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
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

    # --- identity (#30) ---
    #
    # The OIDC issuer URL, e.g. http://localhost:8081/realms/luxmedicine for the
    # docker-compose Keycloak. Only the issuer is configured; the JWKS endpoint is
    # discovered from it, so moving to an EU-hosted managed provider is this one line.
    oidc_issuer: str = ""

    # Which audience a token must carry to be accepted here. Not optional and not
    # cosmetic: without it any token the issuer ever signed is accepted, including other
    # applications' users and service accounts in the same realm.
    #
    # Keycloak note, learned the hard way by everyone: Keycloak does NOT put the client
    # id in `aud` by default — it puts `account`, and the client id lands in `azp`. The
    # realm needs an audience mapper on the client, or every token fails here. That is
    # the intended failure: a token without an audience is a token that was not addressed
    # to us, and accepting it because configuring the mapper was tedious is the whole bug.
    oidc_audience: str = ""

    @model_validator(mode="after")
    def _identity_is_configured_outside_local(self) -> "Settings":
        """Fail at boot, not at request time.

        An unconfigured issuer outside local development is an API that cannot
        authenticate anybody — which is safe, but it is safe in the way that only becomes
        visible when a clinician is locked out at 2am. Better to refuse to start and say
        why. There is deliberately no flag that turns authentication *off* instead; see
        `core/auth.py`.
        """
        if self.environment != "local" and not (self.oidc_issuer and self.oidc_audience):
            raise ValueError(
                f"environment={self.environment!r} requires OIDC_ISSUER and OIDC_AUDIENCE: "
                "actor_id must come from a verified identity, and there is no bypass"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
