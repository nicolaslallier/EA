"""Guards on the deployment files themselves.

The graph no longer runs on the developer's machine: it is a stack deployed on
the Docker host at 192.168.1.252 (see docs/adr/0006). Two invariants of that
move are worth failing a build over, because both are silent when broken:

* the committed stack must not carry a password — the cluster instance is
  shared and reachable on the LAN, so its credentials come from Portainer's
  own environment variables;
* `docker-compose.yml` must not grow a Neo4j anybody could model against, or
  half the team ends up with a private graph. It does declare one since
  docs/adr/0024 — the throwaway graph the integration tests wipe — so the
  guard is on what would turn that into a second model: a published address
  beyond loopback, or data kept in a named volume.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STACK = REPO_ROOT / "deploy" / "neo4j.stack.yml"
COMPOSE = REPO_ROOT / "docker-compose.yml"


def test_the_stack_file_is_committed() -> None:
    assert STACK.is_file(), f"missing {STACK.relative_to(REPO_ROOT)}"


def test_the_stack_takes_its_password_from_the_environment() -> None:
    """`:?` makes Portainer refuse the deployment instead of inventing a password."""
    stack = STACK.read_text(encoding="utf-8")

    assert "${NEO4J_PASSWORD:?" in stack
    assert "${NEO4J_PASSWORD:-" not in stack, "a default password would be a committed secret"
    assert "NEO4J_AUTH: neo4j/${NEO4J_PASSWORD" in stack


def test_the_stack_pins_the_neo4j_image() -> None:
    """An unpinned tag turns a redeploy into an unplanned major upgrade."""
    images = [
        line.strip()
        for line in STACK.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("image:")
    ]

    assert images, "the stack declares no image"
    for image in images:
        assert ":latest" not in image, image
        assert image.count(":") >= 2, f"{image} has no version tag"


def test_the_stack_publishes_bolt_and_the_browser() -> None:
    stack = STACK.read_text(encoding="utf-8")

    assert ":7687" in stack, "the backend talks Bolt; the port must be published"
    assert ":7474" in stack, "the Neo4j browser is how the graph gets inspected"


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
    """One graph, on the cluster — a local one would silently fork the model.

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
