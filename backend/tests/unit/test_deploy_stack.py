"""Guards on the deployment files themselves.

The shared database is the `postgres` service of the Infra stack (docs/adr/0029).
What is left to guard here is worth failing a build over, because it is silent
when broken:

* `docker-compose.yml` must hold nothing but the throwaway PostgreSQL the
  integration tests migrate up and down — since docs/adr/0033 there is no
  graph database left anywhere in this repository.
* `backend/Dockerfile` copies a venv from its build stage into its runtime
  stage, and a venv only works under the Python that built it. A runtime image
  one minor version ahead builds cleanly, then dies at start on
  `No module named 'alembic'` — which is what a Dependabot bump of one `FROM`
  did.
* `deploy/ea.stack.yml` must hand the API the Infra CA, or it cannot fetch
  the realm's signing keys and refuses to boot, and must build the SPA for the
  realm — Vite writes both values into the bundle (docs/adr/0032).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"
BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
STACK = REPO_ROOT / "deploy" / "ea.stack.yml"


def _stack_service(name: str) -> str:
    """One service's block of the deployed stack, without its comments.

    From `  name:` to the next key at the same indentation, so a value found
    here belongs to that service and not to its neighbour.
    """
    lines = [
        line
        for line in STACK.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    start = lines.index(f"  {name}:")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^\S|^  \S", lines[i])),
        len(lines),
    )
    return "\n".join(lines[start:end])


def api_environment() -> dict[str, str]:
    """The `api` service's `environment:` block, one key to its raw value.

    Surrounding quotes are stripped, like the inline `setting()` below; a
    `${VAR:-default}` or `${VAR:?message}` expression has none to strip.
    """
    api = _stack_service("api")
    return {
        key: value.strip("\"'") for key, value in re.findall(r"^\s+(EA_\S+):\s*(.+)$", api, re.M)
    }


def _compose_declarations() -> str:
    """The compose file without its comments, which are expected to explain."""
    return "\n".join(
        line
        for line in COMPOSE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _published_ports() -> list[str]:
    """Every `- "host:container"` entry, quotes stripped."""
    return [
        match.group(1)
        for match in re.finditer(r'^\s*-\s*"([^"]*:\d+)"\s*$', _compose_declarations(), re.M)
    ]


def test_every_local_container_listens_on_loopback_only() -> None:
    """It carries a disposable password in clear; the LAN must not reach it."""
    ports = _published_ports()

    assert ports, "the compose file publishes nothing"
    assert all(port.startswith("127.0.0.1:") for port in ports), ports


def test_no_graph_database_is_left_in_the_deployment_files() -> None:
    """The graph lives in PostgreSQL since docs/adr/0033; Neo4j must not creep back."""
    for path in (COMPOSE, STACK, REPO_ROOT / ".github" / "workflows" / "ci.yml"):
        declarations = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "neo4j" not in declarations.lower(), path


def test_the_api_image_runs_the_python_its_venv_was_built_for() -> None:
    """The build stage's `uv:…-python3.12` and the runtime's `python:3.12.x` agree.

    The venv's packages sit under `lib/python3.12/`; a runtime Python of another
    minor version does not look there, and every import fails at start.
    """
    images = re.findall(r"^FROM\s+(\S+)", BACKEND_DOCKERFILE.read_text(encoding="utf-8"), re.M)
    versions = [re.search(r"python:?(\d+\.\d+)", image) for image in images]

    assert len(images) == 2, images
    assert all(versions), f"no Python version in {images}"
    assert len({match.group(1) for match in versions if match}) == 1, images


def test_the_deployed_api_verifies_tokens_with_the_infra_ca_mounted_read_only() -> None:
    api = _stack_service("api")

    assert re.search(r"^\s+EA_AUTH_CA_CERT: /etc/ssl/certs/infra-ca\.pem\s*$", api, re.M), api
    assert re.search(r"^\s+- \S.*:/etc/ssl/certs/infra-ca\.pem:ro\s*$", api, re.M), api
    enabled = re.search(r"^\s+EA_AUTH_ENABLED:\s*(\S+)\s*$", api, re.M)
    assert enabled is None or enabled.group(1).strip("\"'") == "true", enabled


def test_the_deployed_api_serves_mcp_under_the_vhost_it_is_reached_by() -> None:
    """LibreChat calls `/api/mcp` through the Infra NGINX (docs/adr/0034).

    Behind the proxy every peer is NGINX, so remote clients are allowed and the
    token is the barrier — auth staying on is the test above. The `Host` NGINX
    forwards and the resource the 401 advertises are both the vhost's name, the
    one `EA_CORS_ORIGINS` already names: a drift between them is a 421, or a
    login that discovers nothing.
    """
    api = _stack_service("api")

    def setting(name: str) -> str:
        found = re.search(rf"^\s+{name}:\s*(\S+)\s*$", api, re.M)
        assert found, f"{name} is not set in {api}"
        return found.group(1).strip("\"'")

    hostname = setting("EA_CORS_ORIGINS").removeprefix("https://")
    assert setting("EA_MCP_ENABLED") == "true"
    assert setting("EA_MCP_ALLOW_REMOTE_CLIENTS") == "true"
    assert setting("EA_MCP_ALLOWED_HOSTS") == hostname
    assert setting("EA_MCP_RESOURCE_URL") == f"https://{hostname}/api/mcp"


def test_the_spa_image_is_built_for_the_realm() -> None:
    web = _stack_service("web")
    args = re.search(
        r"^    build:\n(?:      .*\n)*?      args:\n((?:        .*(?:\n|$))+)", web, re.M
    )

    assert args, web
    assert re.search(r"^\s+VITE_AUTH_AUTHORITY: \S", args.group(1), re.M), args.group(1)
    assert re.search(r"^\s+VITE_AUTH_CLIENT_ID: \S", args.group(1), re.M), args.group(1)


def test_the_deployed_api_keeps_its_files_in_the_infra_minio_on_infra_net() -> None:
    """Inside `infra-net` MinIO is the service `minio`, in plain HTTP like `postgres`.

    `minio.famillelallier.net` has no alias there (only Keycloak's name does),
    so the public name would not resolve from the container. See docs/adr/0036.
    """
    env = api_environment()

    assert env["EA_S3_ENABLED"] == "true"
    assert env["EA_S3_ENDPOINT"] == "${EA_S3_ENDPOINT:-minio:9000}"
    assert env["EA_S3_SECURE"] == "false"
    assert env["EA_S3_BUCKET"] == "${EA_S3_BUCKET:-ea-catalogue}"
    assert env["EA_S3_ACCESS_KEY"].startswith("${EA_S3_ACCESS_KEY:?")
    assert env["EA_S3_SECRET_KEY"].startswith("${EA_S3_SECRET_KEY:?")
