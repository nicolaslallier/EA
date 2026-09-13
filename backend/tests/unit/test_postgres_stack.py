"""Guards on the PostgreSQL stack deployed on the cluster.

`test_deploy_stack.py` guards the graph's stack; this is its counterpart for
the relational store of docs/adr/0015. Every invariant below is one whose
breakage is silent — the stack deploys, turns healthy, and is wrong:

* a committed password, on an instance that is shared and reachable on the LAN;
* an image without pgvector, which migration 0003 discovers only after the
  stack looked fine (`CREATE EXTENSION vector`, docs/adr/0019);
* an image that moves under its tag, turning a redeploy into an upgrade nobody
  decided;
* a `PGDATA` that moves, which brings the database back *empty* on a full
  volume;
* a test container on another major than the cluster, so the migrations are
  proven against a server nobody runs.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STACK = REPO_ROOT / "deploy" / "postgres.stack.yml"
COMPOSE = REPO_ROOT / "docker-compose.yml"

PINNED_PGVECTOR = re.compile(r"^pgvector/pgvector:pg(\d+)@sha256:[0-9a-f]{64}$")


def _declarations(path: Path) -> str:
    """The file without its comments, which are expected to explain."""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _postgres_images(path: Path) -> list[str]:
    return [
        image
        for image in re.findall(r"^\s*image:\s*(\S+)\s*$", _declarations(path), re.M)
        if "postgres" in image or "pgvector" in image
    ]


def _stack_image() -> str:
    images = _postgres_images(STACK)
    assert len(images) == 1, f"expected one PostgreSQL image in the stack, found {images}"
    return images[0]


def test_the_stack_file_is_committed() -> None:
    assert STACK.is_file(), f"missing {STACK.relative_to(REPO_ROOT)}"


def test_the_stack_takes_its_password_from_the_environment() -> None:
    """`:?` makes Portainer refuse the deployment instead of inventing a password."""
    stack = _declarations(STACK)

    assert re.search(r"POSTGRES_PASSWORD:\s*\$\{POSTGRES_PASSWORD:\?", stack)
    assert "${POSTGRES_PASSWORD:-" not in stack, "a default password would be a committed secret"
    assert "PGPASSWORD" not in stack


def test_the_image_carries_pgvector() -> None:
    """A `postgres:NN` image deploys, answers `pg_isready`, and fails migration 0003."""
    assert _stack_image().startswith("pgvector/pgvector:"), _stack_image()


def test_the_image_is_pinned_by_tag_and_by_digest() -> None:
    """The tag says which version; the digest says which bytes.

    `pg17` is republished for every minor release, so a tag alone lets a
    redeploy change servers without this file changing. The tag stays beside
    the digest so a reader — and Dependabot — still knows what it is.
    """
    assert PINNED_PGVECTOR.match(_stack_image()), _stack_image()


def test_the_data_directory_does_not_move() -> None:
    """A different `PGDATA` on the same volume starts an empty cluster, quietly."""
    stack = _declarations(STACK)

    assert "PGDATA: /var/lib/postgresql/data/pgdata" in stack
    assert "postgres-data:/var/lib/postgresql/data" in stack


def test_the_healthcheck_proves_the_server_accepts_connections_without_a_password() -> None:
    healthchecks = [line for line in _declarations(STACK).splitlines() if "test:" in line]

    assert healthchecks, "the stack declares no healthcheck"
    for line in healthchecks:
        assert "pg_isready" in line, line
        assert "PASSWORD" not in line, "a healthcheck argument is visible in `ps`"


def test_the_stack_comes_back_after_a_host_reboot() -> None:
    """There is no `make` target that starts the cluster instance again."""
    assert re.search(r"^\s*restart:\s*(unless-stopped|always)\s*$", _declarations(STACK), re.M)


def test_the_stack_publishes_postgres() -> None:
    assert ":5432" in _declarations(STACK)


def test_the_test_container_runs_the_cluster_s_postgres() -> None:
    """Migrations proven on pg17 say nothing about a cluster on pg16, or pg18.

    The local container may follow its tag rather than a digest — it holds
    nothing — but repository and tag must be the cluster's.
    """
    local = _postgres_images(COMPOSE)

    assert local, "docker-compose.yml declares no PostgreSQL for the tests"
    for image in local:
        assert image.split("@")[0] == _stack_image().split("@")[0], (image, _stack_image())
