"""The guards that keep a destructive test off a database somebody uses.

These run everywhere, with no database: they are about the *decision* to touch
one. The database is wiped by the suite in this directory, by `alembic
downgrade base`, and has a shared instance on this Mac since docs/adr/0029,
whose address is the default in `Settings`. A guard that trusted configuration
alone would be one `backend/.env` away from emptying it, so the only address
accepted is one that cannot be another machine. See docs/adr/0024.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.integration.throwaway import is_loopback, refuse_a_shared_minio, refuse_a_shared_postgres

HERE = Path(__file__).parent


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]", "127.0.0.2"]
)
def test_a_loopback_host_is_recognised(host: str) -> None:
    assert is_loopback(host)


@pytest.mark.parametrize(
    "host",
    [
        "192.168.2.10",
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


class TestPostgres:
    def test_a_remote_host_is_refused(self) -> None:
        reason = refuse_a_shared_postgres("192.168.2.10", 5432)

        assert reason is not None
        assert "192.168.2.10" in reason

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_the_shared_instance_is_refused_although_it_is_loopback(self, host: str) -> None:
        """Since docs/adr/0029 the shared database answers on this Mac's 5432.

        Loopback no longer means "a container nobody uses", so the port the
        settings default to — the shared one — is refused on its own.
        """
        reason = refuse_a_shared_postgres(host, 5432)

        assert reason is not None
        assert "5432" in reason

    def test_the_throwaway_instance_is_accepted(self) -> None:
        assert refuse_a_shared_postgres("127.0.0.1", 5433) is None


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


class TestTheMinioGuard:
    @pytest.mark.parametrize("endpoint", ["127.0.0.1:9100", "localhost:9100", "[::1]:9100"])
    def test_the_throwaway_one_is_accepted(self, endpoint: str) -> None:
        assert refuse_a_shared_minio(endpoint) is None

    @pytest.mark.parametrize(
        "endpoint",
        [
            "minio.famillelallier.net",
            "minio:9000",
            "127.0.0.1:9000",
            "127.0.0.1",
            "192.168.2.10:9100",
        ],
    )
    def test_anything_else_may_be_a_bucket_somebody_uses(self, endpoint: str) -> None:
        assert "9100" in (refuse_a_shared_minio(endpoint) or "")
