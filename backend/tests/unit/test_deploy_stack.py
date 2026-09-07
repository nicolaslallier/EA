"""Guards on the deployment files themselves.

The graph no longer runs on the developer's machine: it is a stack deployed on
the Docker host at 192.168.1.252 (see docs/adr/0006). Two invariants of that
move are worth failing a build over, because both are silent when broken:

* the committed stack must not carry a password — the cluster instance is
  shared and reachable on the LAN, so its credentials come from Portainer's
  own environment variables;
* `docker-compose.yml` must not grow a `neo4j` service again, or half the team
  ends up modelling against a private graph.
"""

from __future__ import annotations

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


def test_the_local_compose_file_no_longer_runs_neo4j() -> None:
    """One graph, on the cluster — a local one would silently fork the model.

    Comments are stripped first: the file is expected to *explain* where the
    graph went, it just must not declare it.
    """
    declarations = [
        line
        for line in COMPOSE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]

    assert "neo4j" not in "\n".join(declarations).lower()
