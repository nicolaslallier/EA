"""Application settings, read from the environment only.

No literal secret, DSN or key lives in this file — see `.env.example` for the
shape of a local environment.
"""

from typing import Annotated

from pydantic import SecretStr, ValidationInfo, field_validator, model_validator
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

    # Every interface, not loopback: the API is called from other machines on
    # the LAN — a peer's browser, the cluster, a phone. What this does *not*
    # decide is who may call `/mcp`; that is `mcp_allowed_hosts` below, and the
    # two are kept apart on purpose.
    host: str = "0.0.0.0"
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

    # --- PostgreSQL, the store for everything that is not the graph ---------
    # Auth, audit and scheduled work live here rather than in Neo4j — see
    # docs/adr/0004 for the split, docs/adr/0015 for this scaffold.
    #
    # Like the graph, there is one instance and it is on the Docker cluster, so
    # its address is the useful default: a developer who never writes a `.env`
    # reaches the shared database rather than a `localhost` that answers
    # nothing. The password below has no default — that one is a real shared
    # secret, not a throwaway.
    #
    # `postgres_enabled` is on since the first table landed: `element_documents`
    # holds the markdown attached to the elements of the graph (docs/adr/0017),
    # so a deployment without PostgreSQL is now a misconfiguration rather than
    # the normal case — exactly as it already is for the graph. A process that
    # cannot reach the cluster fails at boot, loudly, instead of on the first
    # upload of the first user.
    postgres_enabled: bool = True
    postgres_host: str = "192.168.1.252"
    postgres_port: int = 5432
    postgres_user: str = "ea"
    postgres_password: SecretStr = SecretStr("")
    postgres_database: str = "ea"

    # The pool bounds a slow or wedged server the same way the Neo4j one does.
    postgres_pool_size: int = 5
    postgres_max_overflow: int = 5
    postgres_connection_timeout_seconds: float = 5.0

    # --- The MCP adapter, mounted on this app at /mcp — see docs/adr/0014 ---
    # On by default: an agent-facing tool set nobody can reach is not a
    # feature. It is a switch and not a constant because, until auth exists,
    # `/mcp` is an unauthenticated *write* path onto the architecture graph for
    # anyone who can reach this host — a deployment that does not want that
    # turns it off here rather than by deleting a mount.
    mcp_enabled: bool = True

    #: Which `Host` headers the MCP transport answers, as an explicit allowlist.
    #:
    #: The SDK enables DNS-rebinding protection by itself *only* when it is
    #: served on a loopback host, so handing it `host` would silently disable
    #: the protection the day the API binds every interface — on a path that
    #: writes to the graph without authentication. It is therefore its own
    #: setting, defaulting to loopback: an agent on another machine is added
    #: here deliberately, the way a CORS origin is.
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    ]

    @field_validator("cors_origins", "mcp_allowed_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept `A,B` from the environment as well as a real list."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @field_validator("cors_origins", "mcp_allowed_hosts", mode="after")
    @classmethod
    def _reject_wildcard(cls, value: list[str], info: ValidationInfo) -> list[str]:
        """A bare `*` is the check switched off, which is never what is meant.

        Entries like `localhost:*` stay legal: the wildcard is on the port, and
        the host itself is still named.
        """
        if "*" in value:
            msg = f"{info.field_name} must be an explicit allowlist, not a wildcard"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_a_neo4j_password_outside_debug(self) -> "Settings":
        """A deployed instance talking to an unauthenticated database is a breach."""
        if not self.debug and not self.neo4j_password.get_secret_value():
            msg = "neo4j_password is required when debug is off"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _require_a_postgres_password_when_the_store_is_used(self) -> "Settings":
        """Same rule as the graph, but owed only by a process that connects.

        The condition is `postgres_enabled` and not the mere presence of the
        settings: demanding a secret for a database this process never opens
        would make every developer invent one to start the API.
        """
        if (
            self.postgres_enabled
            and not self.debug
            and not self.postgres_password.get_secret_value()
        ):
            msg = "postgres_password is required when postgres_enabled is on and debug is off"
            raise ValueError(msg)
        return self


def get_settings() -> Settings:
    """Build the settings for this process."""
    return Settings()
