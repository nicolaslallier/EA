"""Application settings, read from the environment only.

No literal secret, DSN or key lives in this file — see `.env.example` for the
shape of a local environment.
"""

from typing import Annotated

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment or a local `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="EA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "EA API"
    debug: bool = False

    host: str = "127.0.0.1"
    port: int = 8000

    # Explicit allowlist — never `*`, because the API is called with credentials.
    # `NoDecode` keeps pydantic-settings from JSON-decoding the environment
    # value: without it `EA_CORS_ORIGINS=http://localhost:5173` — the form
    # `.env.example` ships — raises before the validator below ever runs.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # --- Neo4j, the store of the architecture graph — see docs/adr/0004 ------
    # There is one instance, on the Docker cluster (docs/adr/0006), so its
    # address is the useful default: a developer who never writes a `.env`
    # reaches the shared graph rather than a `localhost` that answers nothing.
    # It is an address, not a credential — the password below has no default,
    # and an empty one is only tolerated in debug.
    neo4j_uri: str = "bolt://192.168.1.252:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = "neo4j"

    # Bolt keeps connections pooled; these bound a slow or wedged server.
    neo4j_max_connection_pool_size: int = 25
    neo4j_connection_timeout_seconds: float = 5.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept `A,B` from the environment as well as a real list."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins", mode="after")
    @classmethod
    def _reject_wildcard(cls, value: list[str]) -> list[str]:
        if "*" in value:
            msg = "cors_origins must be an explicit allowlist, not a wildcard"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_a_neo4j_password_outside_debug(self) -> "Settings":
        """A deployed instance talking to an unauthenticated database is a breach."""
        if not self.debug and not self.neo4j_password.get_secret_value():
            msg = "neo4j_password is required when debug is off"
            raise ValueError(msg)
        return self


def get_settings() -> Settings:
    """Build the settings for this process."""
    return Settings()
