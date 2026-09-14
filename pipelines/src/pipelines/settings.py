"""Configuration for the pipelines process, read from the environment.

Every field is `PIPELINES_<NAME>` (case-insensitive), from the environment
only — never from a literal in code, the same rule
`backend/src/ea/core/config.py` follows. The four secrets have no default:
building a `Settings` without them fails loudly, at start-up, rather than on
the first call that needed one.

No `env_file`: `pipelines/.env` feeds `docker compose`, which hands the worker
only its own `PIPELINES_*` variables. That file also holds keys this class has
no field for (`LITELLM_DATABASE_URL`, ...), which `extra="forbid"` would refuse,
and reading it would let a developer's file leak into every test run.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything a flow needs to reach the EA API, LiteLLM and MinIO."""

    model_config = SettingsConfigDict(env_prefix="PIPELINES_")

    #: The EA API, reached over HTTP only — never `/mcp`, never a repository
    #: (docs/adr/0028). `host.docker.internal` is the default because the
    #: pipeline runs in Docker while `make run-be` binds the Mac's own ports.
    ea_base_url: str = "http://host.docker.internal:8000"

    #: LiteLLM, behind the `smart` and `fast` aliases — the code never names a
    #: real model (see `pipelines/litellm.yaml`).
    litellm_base_url: str = "http://litellm:4000"
    litellm_api_key: SecretStr

    llm_timeout_seconds: float = 300
    ea_timeout_seconds: float = 30

    #: MinIO of the `~/OpenCode/Infra` stack, read-only from this project.
    s3_endpoint: str = "minio.famillelallier.net"
    s3_secure: bool = True
    s3_access_key: SecretStr
    s3_secret_key: SecretStr
    s3_bucket: str = "ea-catalogue"
    s3_ca_cert: str | None = None

    #: A source longer than this many characters is refused, never truncated,
    #: before it reaches the LLM — the budget of a prompt, not of the document.
    max_source_chars: int = 60000

    #: The realm `ea` of the Infra Keycloak. The worker logs in as the
    #: confidential client `ea-pipelines`, whose service account holds
    #: `ea-editor` (EA docs/adr/0032). The token endpoint derives from this.
    auth_issuer: str = "https://keycloak.famillelallier.net/realms/ea"
    ea_client_id: str = "ea-pipelines"
    ea_client_secret: SecretStr
