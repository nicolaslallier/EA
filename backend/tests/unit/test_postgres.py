"""The relational engine: how it is addressed, built and checked.

PostgreSQL is the store for everything that is not the architecture graph —
auth, audit, scheduled work (docs/adr/0004). No table exists yet; what is
tested here is the connection itself, and above all that the password it
carries cannot escape into a log line or a traceback.
"""

from __future__ import annotations

from typing import Any

import pytest
from asyncpg.exceptions import InvalidPasswordError
from sqlalchemy.exc import OperationalError

from ea.api.dependencies import session_factory_of
from ea.core.config import Settings
from ea.db.postgres import (
    RelationalStoreUnavailableError,
    check_connectivity,
    create_engine,
    create_session_factory,
    dsn_of,
)
from ea.main import create_app
from ea.services.architecture import ArchitectureService

SETTINGS = Settings(
    debug=True,
    postgres_host="db.internal",
    postgres_port=5433,
    postgres_user="ea",
    postgres_password="s3cret",
    postgres_database="ea",
)


def test_the_dsn_is_assembled_from_the_settings() -> None:
    dsn = dsn_of(SETTINGS)

    assert dsn.drivername == "postgresql+asyncpg"
    assert (dsn.host, dsn.port, dsn.username, dsn.database) == ("db.internal", 5433, "ea", "ea")


def test_the_dsn_does_not_print_its_password() -> None:
    """A DSN reaches log lines and `repr` of engines; the secret must not."""
    dsn = dsn_of(SETTINGS)

    assert "s3cret" not in str(dsn)
    assert "s3cret" not in repr(dsn)


def test_the_dsn_still_carries_the_password_when_asked_for_it() -> None:
    """Hiding it in `str` is worthless if the driver cannot get the real one."""
    assert "s3cret" in dsn_of(SETTINGS).render_as_string(hide_password=False)


def test_a_password_full_of_url_syntax_survives_the_dsn() -> None:
    """`p@ss/word:1` hand-spliced into a URL string builds a different database."""
    settings = Settings(debug=True, postgres_password="p@ss/word:1", postgres_database="ea")

    dsn = dsn_of(settings)

    assert dsn.password == "p@ss/word:1"
    assert dsn.database == "ea"


def test_the_engine_is_built_from_settings_without_connecting() -> None:
    """Building an engine must not do I/O, or `create_app` would block on boot."""
    engine: Any = create_engine(SETTINGS)

    assert engine.url.database == "ea"


def test_the_engine_never_prints_its_password() -> None:
    engine = create_engine(SETTINGS)

    assert "s3cret" not in repr(engine)
    assert "s3cret" not in str(engine.url)


def test_the_session_factory_keeps_objects_usable_after_a_commit() -> None:
    """`expire_on_commit` would re-query every attribute read after a commit."""
    factory = create_session_factory(create_engine(SETTINGS))

    assert factory.kw["expire_on_commit"] is False


class UnreachableEngine:
    """An engine whose every connection attempt is refused."""

    def connect(self) -> Any:
        return _RefusedConnection()


class _RefusedConnection:
    async def __aenter__(self) -> Any:
        raise OperationalError(
            "SELECT 1",
            {},
            OSError('connection to server at "db.internal", port 5433 failed for user "ea"'),
        )

    async def __aexit__(self, *_: object) -> None:
        return None


class RejectingEngine:
    """An engine whose driver refuses the credentials in its own exception type."""

    def connect(self) -> Any:
        return _RejectedConnection()


class _RejectedConnection:
    async def __aenter__(self) -> Any:
        raise InvalidPasswordError('password authentication failed for user "ea"')

    async def __aexit__(self, *_: object) -> None:
        return None


@pytest.mark.asyncio
async def test_an_unreachable_store_stops_the_boot() -> None:
    with pytest.raises(RelationalStoreUnavailableError):
        await check_connectivity(UnreachableEngine())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_failure_message_leaks_neither_host_nor_credentials() -> None:
    """The address belongs in the log; the caller gets a generic sentence."""
    with pytest.raises(RelationalStoreUnavailableError) as caught:
        await check_connectivity(UnreachableEngine())  # type: ignore[arg-type]

    assert "db.internal" not in str(caught.value)
    assert "5433" not in str(caught.value)
    assert "ea" not in str(caught.value).split()


@pytest.mark.asyncio
async def test_a_route_is_handed_a_session_from_the_factory_on_the_app() -> None:
    """The seam the first table will use: no route sees the engine.

    Opening a session does no I/O — the connection is checked out on the first
    statement — so this stays a unit test.
    """
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from ea.api.dependencies import get_session

    app = FastAPI()
    app.state.db_sessions = create_session_factory(create_engine(SETTINGS))
    request = _RequestOn(app)

    sessions = [session async for session in get_session(request)]  # type: ignore[arg-type]

    assert len(sessions) == 1
    assert isinstance(sessions[0], AsyncSession)


@pytest.mark.asyncio
async def test_asking_for_a_session_on_an_app_without_postgres_says_so() -> None:
    """`EA_POSTGRES_ENABLED` off and a route asking for a session is a wiring bug."""
    from fastapi import FastAPI

    from ea.api.dependencies import get_session

    with pytest.raises(RuntimeError, match="postgres_enabled"):
        async for _ in get_session(_RequestOn(FastAPI())):  # type: ignore[arg-type]
            pass


class _RequestOn:
    """The one attribute `get_session` reads off a request."""

    def __init__(self, app: Any) -> None:
        self.app = app


@pytest.mark.asyncio
async def test_a_driver_exception_outside_sqlalchemy_still_stops_the_boot() -> None:
    """Regression: asyncpg raises `InvalidPasswordError`, not a `SQLAlchemyError`.

    Catching only SQLAlchemy's own hierarchy let a wrong password escape as
    itself — naming the role, and crashing a boot with a driver traceback
    instead of the one sentence this module promises.
    """
    with pytest.raises(RelationalStoreUnavailableError) as caught:
        await check_connectivity(RejectingEngine())  # type: ignore[arg-type]

    assert "ea" not in str(caught.value).split()


class WorkingEngine:
    """An engine whose connection answers, and which records its disposal."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.disposed = False

    def connect(self) -> Any:
        return _WorkingConnection(self)

    async def dispose(self) -> None:
        self.disposed = True


class _WorkingConnection:
    def __init__(self, engine: WorkingEngine) -> None:
        self._engine = engine

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement: Any) -> None:
        self._engine.statements.append(str(statement))


@pytest.mark.asyncio
async def test_a_reachable_store_passes_the_check_by_running_a_statement() -> None:
    """A pool that hands out a connection proves nothing until one is used."""
    engine = WorkingEngine()

    await check_connectivity(engine)  # type: ignore[arg-type]

    assert engine.statements == ["SELECT 1"]


@pytest.mark.asyncio
async def test_the_lifespan_opens_the_store_and_disposes_it_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    service: ArchitectureService,
) -> None:
    """`postgres_enabled` on: a session factory is attached and the pool closed.

    The engine is a double because the wiring is what is under test — that the
    lifespan checks *before* publishing the factory, and disposes on the way
    out. The real pool is exercised in `tests/integration/test_postgres.py`.
    """
    engine = WorkingEngine()
    monkeypatch.setattr("ea.main.create_engine", lambda _settings: engine)

    app = create_app(
        Settings(debug=True, postgres_enabled=True, mcp_enabled=False),
        architecture_service=service,
    )
    async with app.router.lifespan_context(app):
        attached = session_factory_of(app)

    assert attached is not None
    assert engine.statements == ["SELECT 1"]
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_the_lifespan_leaves_the_store_shut_while_it_is_disabled(
    service: ArchitectureService,
) -> None:
    """The default: no table, no pool, and a clear failure if a route asks."""
    app = create_app(
        Settings(debug=True, postgres_enabled=False, mcp_enabled=False),
        architecture_service=service,
    )

    async with app.router.lifespan_context(app):
        with pytest.raises(RuntimeError, match="postgres_enabled"):
            session_factory_of(app)
