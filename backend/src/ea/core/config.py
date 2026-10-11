"""Application settings, read from the environment only.

No literal secret, DSN or key lives in this file — see `.env.example` for the
shape of a local environment.
"""

import json
import logging
import re
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from ea.domain.egress import networks_from
from ea.domain.exchange import (
    EXPORT_LEASE_SECONDS,
    EXPORT_SEND_TIMEOUT_SECONDS,
    IMPORT_LEASE_SECONDS,
    MAX_EXCHANGE_BYTES,
    MAX_EXPORTS_IN_FLIGHT,
    MAX_EXPORTS_PER_CALLER,
    MAX_IMPORTS_IN_FLIGHT,
    MAX_IMPORTS_PER_CALLER,
    MAX_NEW_FOLDERS,
)
from ea.domain.external import (
    GITHUB_REPO_PATTERN,
    JIRA_PROJECT_PATTERN,
    SERVICENOW_TABLES,
    TARGET_NAME_PATTERN,
    WRITABLE_KINDS,
    ExternalKind,
)
from ea.domain.packages import MAX_PACKAGES

_CONFLUENCE_SPACE = re.compile(r"[1-9][0-9]{0,15}")
_NEEDS_A_USER = frozenset({ExternalKind.JIRA_ISSUE, ExternalKind.SERVICENOW_TICKET})
MAX_TOKEN_LENGTH = 4096


class ExternalTargetSettings(BaseModel):
    """One third-party system EA may call, as the deployment declares it.

    The caller of an API never writes any of this: it names the target, and the host, the
    scope and the account are these (docs/adr/0063). The secret is not here: it is in
    `Settings.external_tokens`, under the same name.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    kind: ExternalKind
    #: Where the server calls — the only host it ever contacts for this target.
    api_url: str
    #: The site the *displayed* address is built on; the only host a pasted address is read from.
    web_url: str
    #: What may be read: repositories, projects, table prefixes, space ids. Empty = inactive.
    allow: list[str] = []
    #: The account the token belongs to (Jira's e-mail, ServiceNow's user). Not a secret.
    user: str = Field(default="", max_length=320)
    #: Whether the server may open tickets here — the token must then be allowed to write.
    writable: bool = False
    #: The ports the host may be reached on; 443 unless the deployment says otherwise.
    ports: list[int] = [443]
    #: Private networks (CIDR) the host may resolve into. Loopback and link-local never.
    allow_networks: list[str] = []

    @field_validator("kind", mode="after")
    @classmethod
    def _a_link_has_no_connector(cls, value: ExternalKind) -> ExternalKind:
        if value is ExternalKind.URL:
            msg = "a target has a kind with a connector; a free link needs none"
            raise ValueError(msg)
        return value

    @field_validator("writable", mode="before")
    @classmethod
    def _writable_only_when_really_true(cls, value: object) -> bool:
        """Fail closed: a string, a number or anything else that is not `true` is read-only."""
        return value is True

    @field_validator("ports", mode="after")
    @classmethod
    def _ports_are_ports(cls, value: list[int]) -> list[int]:
        if not value or any(not 0 < port < 65536 for port in value):
            msg = "ports must be a non-empty list of TCP ports"
            raise ValueError(msg)
        return value

    @field_validator("allow_networks", mode="after")
    @classmethod
    def _networks_are_declarable(cls, value: list[str]) -> list[str]:
        networks_from(value)  # raises a ValueError that names the rule, never the value
        return value

    @model_validator(mode="after")
    def _allow_names_things_of_this_kind(self) -> "ExternalTargetSettings":
        normal: list[str] = []
        for entry in self.allow:
            if self.kind is ExternalKind.GITHUB_ISSUE:
                found = entry.strip().lower()
                ok = bool(re.fullmatch(GITHUB_REPO_PATTERN, found)) and not found.endswith(
                    ("/.", "/..")
                )
            elif self.kind is ExternalKind.JIRA_ISSUE:
                found = entry.strip().upper()
                ok = bool(re.fullmatch(JIRA_PROJECT_PATTERN, found))
            elif self.kind is ExternalKind.SERVICENOW_TICKET:
                found = entry.strip().upper()
                ok = found in SERVICENOW_TABLES
            else:
                found = entry.strip()
                ok = bool(_CONFLUENCE_SPACE.fullmatch(found))
            if not ok:
                msg = f"an allow entry is not a valid scope for a {self.kind.value} target"
                raise ValueError(msg)
            normal.append(found)
        object.__setattr__(self, "allow", normal)
        return self

    @model_validator(mode="after")
    def _only_some_kinds_can_open_tickets(self) -> "ExternalTargetSettings":
        if self.writable and self.kind not in WRITABLE_KINDS:
            msg = f"a {self.kind.value} target cannot be writable: it has no way to open tickets"
            raise ValueError(msg)
        return self

    @property
    def needs_a_user(self) -> bool:
        return self.kind in _NEEDS_A_USER


def _site_problem(url: str, *, ports: list[int]) -> str | None:
    """What is wrong with a target's address, in words that do not repeat it."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "is not an address"
    if parts.scheme != "https":
        return "must be https"
    if not parts.hostname:
        return "has no host"
    if parts.username is not None or parts.password is not None:
        return "must not carry credentials"
    if parts.query or parts.fragment:
        return "must not carry a query or a fragment"
    if (port or 443) not in ports:
        return "uses a port the target does not declare (port)"
    return None


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment or a local `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="EA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # A validation error must never repeat what it refused: a malformed
        # EA_EXTERNAL_TOKENS holds the tokens themselves (docs/adr/0063).
        hide_input_in_errors=True,
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
    # How long a statement of the API waits for a row lock before PostgreSQL gives
    # up (SQLSTATE 55P03, answered 409 write_conflict). Ten seconds, under NGINX's
    # 60 s `proxy_read_timeout`: with 5+5 pooled connections, ten writes queued
    # behind a long import's locks would otherwise freeze every read and `/health`
    # and end in a 504 for a write that goes through later. 0 turns it off.
    # Only the API's engine asks for it (`create_api_engine`) — docs/adr/0052.
    db_lock_timeout_ms: int = Field(default=10_000, ge=0)

    # --- Exchange files (docs/adr/0050) ------------------------------------
    # The largest exchange file an import accepts, cut while it is received. A setting
    # because a deployment sits behind a proxy with its own ceiling; read once, at boot.
    # The upper bound is the point at which a file is read in a thread for minutes and
    # holds hundreds of megabytes: past it, split the file.
    exchange_max_bytes: int = Field(default=MAX_EXCHANGE_BYTES, ge=1024, le=100 * 1024 * 1024)
    # How long an import's client may stay silent between two pieces of its body, and how
    # long the whole body may take to arrive (docs/adr/0050). A place is held while the body
    # is read: without these, two slow connections hold every place. They run only during the
    # reception, not during the analysis, and add to the proxy's `client_body_timeout`.
    # Packages one import may create: each costs a reading of the tree under the locks of the
    # import and of the tree (docs/adr/0054). Those that already exist do not count.
    exchange_max_new_folders: int = Field(default=MAX_NEW_FOLDERS, ge=0, le=MAX_PACKAGES)
    # Downloads in a process, and how long a client has to take one (docs/adr/0050): neither
    # holds a database connection, only a file and a place.
    exchange_max_exports: int = Field(default=MAX_EXPORTS_IN_FLIGHT, ge=1, le=64)
    exchange_send_timeout_s: float = Field(default=EXPORT_SEND_TIMEOUT_SECONDS, ge=0.01, le=3600.0)
    # One caller's own places (the `sub` of the verified token), and when a place that was never
    # given back is taken back (docs/adr/0050).
    exchange_imports_per_caller: int = Field(
        default=MAX_IMPORTS_PER_CALLER, ge=1, le=MAX_IMPORTS_IN_FLIGHT
    )
    exchange_exports_per_caller: int = Field(default=MAX_EXPORTS_PER_CALLER, ge=1, le=64)
    exchange_import_lease_s: float = Field(default=IMPORT_LEASE_SECONDS, ge=60.0, le=7200.0)
    exchange_export_lease_s: float = Field(default=EXPORT_LEASE_SECONDS, ge=60.0, le=7200.0)
    exchange_read_timeout_s: float = Field(default=15.0, ge=0.01, le=60.0)
    exchange_read_total_s: float = Field(default=120.0, ge=0.05, le=600.0)

    # --- The embedding service, behind the document search — docs/adr/0019 --
    # Ollama on the cluster, beside the graph, serving an OpenAI-compatible
    # `/v1/embeddings` on 11434, Ollama's own port — docs/adr/0038 wrote 11435
    # without probing it, and docs/adr/0040 is the correction. The base URL is
    # all that ties us to it: LM Studio, text-embeddings-inference and the
    # hosted providers answer the same shape, so changing supplier is this line
    # and a model name — which is exactly what docs/adr/0038 was.
    #
    # The `/v1` suffix is part of it: `HttpEmbedder` appends `/embeddings` and
    # nothing else, so a base URL without it reaches a 404 rather than the
    # model.
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
    embeddings_base_url: str = "http://192.168.2.10:11434/v1"

    #: The same model docs/adr/0019 measured — 4 of 5 questions answered at
    #: rank 1 on French runbook prose with its heading trail, against 2 of 5
    #: for `nomic-embed-text-v1.5` — under the name Ollama pulls it by. It is
    #: 1024-wide, which is what the column is. This string is stored beside
    #: every vector and filtered on at search time, so changing it is a full
    #: `make docs-reindex` — see docs/adr/0038.
    embeddings_model: str = "mxbai-embed-large"

    #: Empty for a self-hosted Ollama, which authenticates nothing. It is a
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
    #: Whoever last changed an element cannot approve it (docs/adr/0048). Only an
    #: explicit `false` turns this off — for a repository with a single user; a
    #: missing fact about who changed it never does.
    approval_separation: bool = True
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

    # --- Third-party systems, by NAMED targets — see docs/adr/0063 ---
    # A caller never gives an address: it names a target declared here, and the host, the
    # scope (repositories, projects) and the account are the deployment's. `EA_EXTERNAL_TARGETS`
    # is JSON, `{"name": {"kind": ..., "api_url": ..., "web_url": ..., "allow": [...]}}`;
    # `EA_EXTERNAL_TOKENS` is JSON too, `{"name": "secret"}`, and is the only place a
    # secret is written. A target with no token or no allow-list is declared but inactive.
    external_targets: Annotated[dict[str, ExternalTargetSettings], NoDecode] = {}
    external_tokens: Annotated[dict[str, SecretStr], NoDecode] = {}
    #: Connection and read timeouts per request, and the ceiling for the whole call.
    external_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    external_total_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    #: A response is read up to this size, in a stream, and refused beyond it.
    external_max_response_bytes: int = Field(default=1_000_000, gt=0, le=5_000_000)
    #: Calls to third parties in flight in this process, and per caller (`Admission`).
    external_max_in_flight: int = Field(default=8, ge=1, le=64)
    external_per_caller: int = Field(default=2, ge=1)
    #: The CA that vouches for the providers' certificates when the system store does not
    #: (an on-premises Jira); TLS is always verified.
    external_ca_cert: str | None = None

    # --- Model quality checks — see docs/adr/0057 ---
    #: How alike two names of one type must be to be reported as probable
    #: duplicates (0 = anything, 1 = identical once normalised).
    quality_similarity: float = Field(default=0.9, ge=0.0, le=1.0)
    #: Properties a type must carry, as JSON: {"application_component": ["owner"]}.
    #: Which types and keys are valid is checked at boot by `QualityParams`.
    quality_required: dict[str, list[str]] = {}

    @field_validator("cors_origins", "mcp_allowed_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept `A,B` from the environment as well as a real list."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @field_validator("external_targets", "external_tokens", mode="before")
    @classmethod
    def _json_from_the_environment(cls, value: object, info: ValidationInfo) -> object:
        """The environment carries a JSON object; blank means none (a stack may leave it empty)."""
        if not isinstance(value, str):
            return value
        if not value.strip():
            return {}
        try:
            return json.loads(value)
        except ValueError:
            msg = f"{info.field_name} is not valid JSON"  # never the text: it may hold tokens
            raise ValueError(msg) from None

    @field_validator("external_tokens", mode="before")
    @classmethod
    def _tokens_are_text(cls, value: object) -> object:
        if isinstance(value, dict) and not all(isinstance(token, str) for token in value.values()):
            msg = "external_tokens must map a target name to a text token"
            raise ValueError(msg)
        return value

    @field_validator("external_tokens", mode="after")
    @classmethod
    def _tokens_cannot_split_a_header(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        for token in value.values():
            secret = token.get_secret_value()
            if not secret or len(secret) > MAX_TOKEN_LENGTH or not secret.isprintable():
                msg = "external_tokens: a token is empty, too long, or holds a control character"
                raise ValueError(msg)
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
    def _external_targets_are_declared_soundly(self) -> "Settings":
        """Names, addresses and tokens are checked here — and none of them is repeated."""
        for name, target in self.external_targets.items():
            if not re.fullmatch(TARGET_NAME_PATTERN, name):
                msg = "a target name is a short lowercase slug (letters, digits, hyphens)"
                raise ValueError(msg)
            for label, url in (("api_url", target.api_url), ("web_url", target.web_url)):
                problem = _site_problem(url, ports=target.ports)
                if problem:
                    msg = f"external target {label} {problem}"
                    raise ValueError(msg)
        if set(self.external_tokens) - set(self.external_targets):
            msg = "external_tokens names a target that external_targets does not declare"
            raise ValueError(msg)
        if self.external_per_caller > self.external_max_in_flight:
            msg = "external_per_caller cannot exceed external_max_in_flight"
            raise ValueError(msg)
        return self

    def external_active_targets(self) -> dict[str, ExternalTargetSettings]:
        """The declared targets that can be called: a token, a scope, and the account if needed."""
        return {
            name: target
            for name, target in self.external_targets.items()
            if name in self.external_tokens
            and target.allow
            and (target.user or not target.needs_a_user)
        }

    @model_validator(mode="after")
    def _a_caller_never_has_more_places_than_exist(self) -> "Settings":
        if self.exchange_exports_per_caller > self.exchange_max_exports:
            msg = "exchange_exports_per_caller cannot exceed exchange_max_exports"
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
