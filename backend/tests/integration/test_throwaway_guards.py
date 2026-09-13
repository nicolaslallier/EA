"""The guards that keep a destructive test off a database somebody uses.

These run everywhere, with no database: they are about the *decision* to touch
one. Both stores are wiped by the suite in this directory — the graph between
every case, PostgreSQL by `alembic downgrade base` — and both have a shared
instance on the cluster whose address is the default in `Settings`. A guard
that trusted configuration alone would be one `backend/.env` away from
emptying it, so the only address accepted is one that cannot be another
machine. See docs/adr/0024.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.integration.throwaway import (
    is_loopback,
    refuse_a_shared_graph,
    refuse_a_shared_postgres,
)

HERE = Path(__file__).parent


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]", "127.0.0.2"]
)
def test_a_loopback_host_is_recognised(host: str) -> None:
    assert is_loopback(host)


@pytest.mark.parametrize(
    "host",
    [
        "192.168.1.252",
        "host.docker.internal",
        "localhost.example.com",
        "10.0.0.1",
        "0.0.0.0",
        "",
    ],
)
def test_anything_else_is_not(host: str) -> None:
    """`0.0.0.0` is a bind address, not a place; `host.docker.internal` is the Mac."""
    assert not is_loopback(host)


class TestTheGraph:
    def test_it_is_refused_without_the_explicit_opt_in(self) -> None:
        reason = refuse_a_shared_graph("bolt://127.0.0.1:7688", allow_destructive=None)

        assert reason is not None
        assert "EA_ALLOW_DESTRUCTIVE_TESTS" in reason

    def test_the_cluster_is_refused_even_with_the_opt_in(self) -> None:
        """The opt-in says "I mean it"; it does not say *where*."""
        reason = refuse_a_shared_graph("bolt://192.168.1.252:7687", allow_destructive="1")

        assert reason is not None
        assert "192.168.1.252" in reason

    @pytest.mark.parametrize(
        "uri", ["bolt://127.0.0.1:7688", "neo4j://localhost:7688", "bolt://[::1]:7688"]
    )
    def test_a_local_instance_with_the_opt_in_is_accepted(self, uri: str) -> None:
        assert refuse_a_shared_graph(uri, allow_destructive="1") is None


class TestPostgres:
    def test_the_cluster_is_refused(self) -> None:
        reason = refuse_a_shared_postgres("192.168.1.252", 5432)

        assert reason is not None
        assert "192.168.1.252" in reason

    def test_a_local_instance_is_accepted(self) -> None:
        assert refuse_a_shared_postgres("127.0.0.1", 5432) is None


def test_no_test_builds_its_own_alembic_config() -> None:
    """Alembic's `env.py` reads `Settings`, so a `Config` is a loaded weapon.

    The only one in this suite comes from the `alembic_config` fixture, which
    stands behind the guarded engine; a test building its own would migrate
    whatever `backend/.env` names, guard or no guard.
    """
    offenders = [
        path.name
        for path in HERE.glob("*.py")
        if path.name not in {"conftest.py", Path(__file__).name}
        and "Config(" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_the_alembic_config_fixture_stands_behind_the_guarded_engine() -> None:
    conftest = ast.parse((HERE / "conftest.py").read_text(encoding="utf-8"))
    [fixture] = [
        node
        for node in ast.walk(conftest)
        if isinstance(node, ast.FunctionDef) and node.name == "alembic_config"
    ]

    assert "postgres_engine" in [argument.arg for argument in fixture.args.args]
