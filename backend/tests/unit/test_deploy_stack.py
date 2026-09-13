"""Guards on the deployment files themselves.

The shared graph is the `neo4j` service of the Infra stack, declared in that
repository (see docs/adr/0030). What is left to guard here is worth failing a
build over, because it is silent when broken:

* `docker-compose.yml` must not grow a Neo4j anybody could model against, or
  half the team ends up with a private graph. It does declare one since
  docs/adr/0024 — the throwaway graph the integration tests wipe — so the
  guard is on what would turn that into a second model: a published address
  beyond loopback, or data kept in a named volume.
* `backend/Dockerfile` copies a venv from its build stage into its runtime
  stage, and a venv only works under the Python that built it. A runtime image
  one minor version ahead builds cleanly, then dies at start on
  `No module named 'alembic'` — which is what a Dependabot bump of one `FROM`
  did.
* `deploy/ea.stack.yml` must hand the API the Infra CA, or it cannot fetch
  the realm's signing keys and refuses to boot, and must build the SPA for the
  realm — Vite writes both values into the bundle (docs/adr/0031).
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


def test_the_local_compose_neo4j_is_a_test_instance_not_the_graph() -> None:
    """One graph, in the Infra stack — another one would silently fork the model.

    The compose file does declare a Neo4j since docs/adr/0024, and it is the
    graph the integration tests wipe, never the one anybody models against. Two
    things keep it from becoming a second model: it is published on loopback
    only, so no peer can point a browser or an SPA at it, and it keeps its data
    in no named volume, so there is nothing in it worth keeping.
    """
    compose = _compose_declarations()

    assert "neo4j:" in compose, "the integration tests need their own graph"
    assert "neo4j-data" not in compose, "a named volume would be a graph worth keeping"
    bolt = [port for port in _published_ports() if port.endswith(":7687")]
    assert bolt, "the test graph must publish Bolt"
    assert all(port.startswith("127.0.0.1:") for port in bolt), bolt
    assert not [port for port in _published_ports() if port.endswith(":7474")], (
        "no browser on the test graph: it is not a place to look at a model"
    )


def test_every_local_container_listens_on_loopback_only() -> None:
    """Both carry a disposable password in clear; the LAN must not reach them."""
    ports = _published_ports()

    assert ports, "the compose file publishes nothing"
    assert all(port.startswith("127.0.0.1:") for port in ports), ports


def test_the_test_graph_healthcheck_keeps_the_password_off_the_command_line() -> None:
    """An argument is visible in `ps`; the same rule the Makefile follows."""
    compose = _compose_declarations()

    assert "cypher-shell" in compose
    assert " -p " not in compose
    assert "--password" not in compose


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


def test_the_spa_image_is_built_for_the_realm() -> None:
    web = _stack_service("web")
    args = re.search(
        r"^    build:\n(?:      .*\n)*?      args:\n((?:        .*(?:\n|$))+)", web, re.M
    )

    assert args, web
    assert re.search(r"^\s+VITE_AUTH_AUTHORITY: \S", args.group(1), re.M), args.group(1)
    assert re.search(r"^\s+VITE_AUTH_CLIENT_ID: \S", args.group(1), re.M), args.group(1)
