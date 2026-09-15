"""Application settings, read from the environment only.

No literal secret, DSN or key lives in this file — see `.env.example` for the
shape of a local environment.
"""

import logging
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
    # decide is who may call `/mcp`; that is `mcp_allow_remote_clients` below,
    # and the two are kept apart on purpose. Bandit's B104 flags exactly this
    # bind, which is the decision of docs/adr/0016 rather than an oversight.
    host: str = "0.0.0.0"  # nosec B104
    port: int = 8000

    # Explicit allowlist — never `*`, because the API is called with credentials.
    # `NoDecode` keeps pydantic-settings from JSON-decoding the environment
    # value: without it `EA_CORS_ORIGINS=http://localhost:5173` — the form
    # `.env.example` ships — raises before the validator below ever runs.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # --- What the process prints — see docs/adr/0021 ------------------------
    # The level of our own code. It is INFO by default because the interesting
    # lines are the ones nobody thought to ask for: what the boot opened, what
    # each request did, which tool an agent called. DEBUG adds the traces a
    # developer chasing something wants.
    log_level: str = "INFO"

    #: Text for a human, or one JSON object per line. Left unset it follows
    #: `debug`: `make run` interleaves both servers in one terminal and must
    #: stay readable, while a deployment's logs are read by a collector.
    log_json: bool | None = None

    #: One line per request — method, path, status, duration, request id. On,
    #: because an API with no access log cannot be asked what it just did.
    #: Turning it off hands the job back to uvicorn's own, which knows neither
    #: the duration nor the id.
    log_requests: bool = True

    # The noisy streams, each behind its own switch and each off. They are
    # deliberately *not* opened by `log_level=DEBUG`: a developer wanting to see
    # our own reasoning in detail is not asking for every SELECT and every HTTP
    # round trip at once.
    log_sql: bool = False
    log_embeddings: bool = False

    # --- PostgreSQL ----------------------------------------------------------
    # The one database: the architecture graph (docs/adr/0033), the documents
    # and their index (docs/adr/0017, 0019), the diagrams (docs/adr/0031).
    # Authentication is not here: it is the Keycloak realm `ea`, docs/adr/0032.
    #
    # There is one instance, so its address is the useful
    # default — but not on the cluster: since docs/adr/0029 the `ea` database
    # lives in the `~/OpenCode/Infra` stack on the developer's Mac, behind its
    # NGINX on loopback. A process on another machine needs its own `.env`.
    # The password below has no default — that one is a real shared secret,
    # not a throwaway.
    #
    # `postgres_enabled` is on since the first table landed: `element_documents`
    # holds the markdown attached to the elements of the graph (docs/adr/0017),
    # so a deployment without PostgreSQL is now a misconfiguration rather than
    # the normal case — since it holds the whole model. A process that
    # cannot reach it fails at boot, loudly, instead of on the first
    # upload of the first user.
    postgres_enabled: bool = True
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "ea"
    postgres_password: SecretStr = SecretStr("")
    postgres_database: str = "ea"

    # The pool bounds a slow or wedged server.
    postgres_pool_size: int = 5
    postgres_max_overflow: int = 5
    postgres_connection_timeout_seconds: float = 5.0

    # --- The embedding service, behind the document search — docs/adr/0019 --
    # LM Studio on the cluster, beside the graph, serving an
    # OpenAI-compatible `/v1/embeddings`. The base URL is all that ties us to
    # it: Ollama, text-embeddings-inference and the hosted providers answer the
    # same shape, so changing supplier is this line and a model name.
    #
    # On by default, like the two stores, and for the same reason: a search
    # that silently returns nothing is worse than an API that refuses to start.
    # It is not cross-checked against `postgres_enabled`: the passages are rows
    # in that database, so a deployment without it has no documents to index
    # either, and the lifespan simply opens no embedding client.
    # The width of the vectors is not configurable here — it is the width of
    # the column, `EMBEDDING_DIMENSIONS`, and changing it is a migration plus a
    # full reindex, never an environment variable.
    embeddings_enabled: bool = True
    embeddings_base_url: str = "http://192.168.2.10:1234/v1"

    #: Measured against the other model LM Studio holds, on French runbook
    #: prose with its heading trail: 4 of 5 questions answered at rank 1,
    #: against 2 of 5 for `nomic-embed-text-v1.5`. It is also 1024-wide, which
    #: is what the column is.
    embeddings_model: str = "text-embedding-mxbai-embed-large-v1"

    #: Empty for a local LM Studio, which authenticates nothing. It is a
    #: `SecretStr` all the same so that pointing this at a hosted provider is a
    #: variable rather than a patch.
    embeddings_api_key: SecretStr = SecretStr("")

    #: Generous, because the first request after a cold start loads the model.
    embeddings_timeout_seconds: float = 60.0
    embeddings_batch_size: int = 16

    #: The instructions this model family is trained with. `mxbai` wants one on
    #: the query and none on the passage; the e5 line wants `passage: ` and
    #: `query: `; `bge-m3` wants neither. Getting them the wrong way round
    #: costs nothing visible and a good deal of recall, which is exactly why
    #: they are stated rather than assumed.
    embeddings_passage_prefix: str = ""
    embeddings_query_prefix: str = "Represent this sentence for searching relevant passages: "

    # --- The MCP adapter, mounted on this app at /mcp — see docs/adr/0014 ---
    # On by default: an agent-facing tool set nobody can reach is not a
    # feature. It is a switch and not a constant because `/mcp` is a *write*
    # path onto the architecture graph — behind a token since docs/adr/0032,
    # but a deployment that does not want one at all turns it off here rather
    # than by deleting a mount.
    mcp_enabled: bool = True

    #: Whether `/mcp` answers a caller whose TCP peer is not this machine.
    #:
    #: Off, and it is the setting that decides *where* a tool may be called
    #: from: the token (docs/adr/0032) says who, and this guard stays in front
    #: of it as defence in depth. `mcp_allowed_hosts` below cannot narrow that
    #: — it checks a header the caller writes. The peer address is the one
    #: thing it does not.
    #: Behind a reverse proxy the peer is the proxy, so turning this on there
    #: serves everyone the proxy serves — which the deployed stack does, on
    #: purpose, for LibreChat. See docs/adr/0023 and docs/adr/0034.
    mcp_allow_remote_clients: bool = False

    #: Which `Host` headers the MCP transport answers, as an explicit allowlist.
    #:
    #: This is the defence against DNS rebinding — a page in a browser that
    #: resolves its own name to this machine, and cannot choose the `Host` it
    #: sends — and nothing more: a script sets `Host: localhost:8000` itself,
    #: so who is served is `mcp_allow_remote_clients` above. The SDK enables
    #: the protection by itself *only* when served on a loopback host, so
    #: handing it `host` would silently disable it the day the API binds every
    #: interface. It is therefore its own setting, defaulting to loopback: a
    #: remote agent needs its `Host` added here *and* the opt-in above.
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    ]

    # --- Authentication, by the Keycloak realm `ea` — see docs/adr/0032 ------
    # On by default, like the two stores: an API that anyone on the LAN can
    # write to is the state this replaces. Off is accepted in debug only.
    auth_enabled: bool = True
    auth_issuer: str = "https://keycloak.famillelallier.net/realms/ea"
    auth_audience: str = "ea-api"
    #: The Infra CA. From the Mac, keycloak.famillelallier.net resolves to
    #: 127.0.0.1 behind a certificate the system trust store does not know.
    auth_ca_cert: str | None = None
    auth_timeout_seconds: float = 5.0
    #: The resource identifier `/mcp` publishes (RFC 9728). It names port 8000
    #: and is not derived from the port uvicorn was started on, which no setting
    #: here knows: `make run-be BE_PORT=8001` needs this set with it, or the
    #: published resource no longer matches the URL Claude Code was given.
    mcp_resource_url: str = "http://127.0.0.1:8000/mcp"

    # --- Files, in the Infra's MinIO — see docs/adr/0036 --------------------
    # Off by default: a machine without the keys still boots, and the file
    # endpoints answer 503 instead of the whole API refusing to start.
    s3_enabled: bool = False
    s3_endpoint: str = "minio.famillelallier.net"
    s3_secure: bool = True
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    #: The bucket `pipelines/` reads: a file put under `inbox/` feeds it.
    s3_bucket: str = "ea-catalogue"
    #: The Infra CA, when the endpoint is reached through the Infra NGINX.
    s3_ca_cert: str | None = None

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

    @field_validator("log_level", mode="after")
    @classmethod
    def _known_level(cls, value: str) -> str:
        """A level nobody recognises would otherwise start the process mute."""
        level = value.strip().upper()
        if level not in logging.getLevelNamesMapping():
            msg = f"log_level must be one of {', '.join(logging.getLevelNamesMapping())}"
            raise ValueError(msg)
        return level

    @property
    def json_logs(self) -> bool:
        """Whether lines come out as JSON — stated if stated, else the opposite of debug."""
        return (not self.debug) if self.log_json is None else self.log_json

    @model_validator(mode="after")
    def _require_a_postgres_password_when_the_store_is_used(self) -> "Settings":
        """A deployed instance talking to an unauthenticated database is a breach.

        Owed only by a process that connects.

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

    @model_validator(mode="after")
    def _authentication_off_only_in_debug(self) -> "Settings":
        """Turning auth off hands every write to whoever reaches the port."""
        if not self.auth_enabled and not self.debug:
            msg = "auth_enabled may only be false when debug is on"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _require_s3_keys_when_the_store_is_used(self) -> "Settings":
        """Anonymous access to a bucket is never what is meant."""
        if self.s3_enabled and not (
            self.s3_access_key.get_secret_value() and self.s3_secret_key.get_secret_value()
        ):
            msg = "EA_S3_ACCESS_KEY and EA_S3_SECRET_KEY are required while EA_S3_ENABLED is on"
            raise ValueError(msg)
        return self


def get_settings() -> Settings:
    """Build the settings for this process."""
    return Settings()
