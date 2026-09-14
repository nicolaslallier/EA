"""Fixtures for the tests that talk to a real Neo4j or a real PostgreSQL.

Both are **throwaway containers** from `docker-compose.yml`, published on
127.0.0.1 and nowhere else — never the shared instances on the Docker cluster
(docs/adr/0024). Every test here destroys what it touches: the graph is emptied
between cases, since Neo4j Community serves a single database and has no
nested transaction to roll back, and the relational tests end with `alembic
downgrade base`, which drops the tables.

That is why the address, not a setting, is the guard. The cluster is the
*default* in `Settings`, and `backend/.env` names it too, so a fixture that
trusted configuration would be one forgotten variable away from emptying the
graph everyone models against. The decision lives in `throwaway.py`, where it
is tested without a database; the fixtures below only act on it:

* `graph_driver` needs `EA_ALLOW_DESTRUCTIVE_TESTS=1` *and* a loopback host in
  `EA_NEO4J_URI` — the opt-in says the caller means it, the address says where;
* `postgres_engine` needs a loopback `EA_POSTGRES_HOST`, and `alembic_config`
  stands behind it, so no migration runs without the guard.

A refusal is a skip whose message names the host, not a failure: a bare
`uv run pytest` stays useful off the network and destroys nothing.
`make test-integration` starts both containers and points at them;
`make test-postgres` does the same for PostgreSQL alone.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.core.config import Settings, get_settings
from ea.db.neo4j import create_driver
from ea.db.postgres import RelationalStoreUnavailableError, create_engine, create_session_factory
from ea.db.postgres import check_connectivity as check_postgres
from ea.db.schema import apply_schema
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.architecture import ArchitectureService
from tests.integration.throwaway import (
    DESTRUCTIVE_OPT_IN,
    refuse_a_shared_graph,
    refuse_a_shared_postgres,
)

WIPE = "MATCH (n:Element) DETACH DELETE n"


@pytest_asyncio.fixture
async def graph_driver() -> AsyncIterator[AsyncDriver]:
    """A driver on the throwaway Neo4j, emptied before and after, or a skip.

    The guard runs before the driver is even built: refusing a shared graph
    must not depend on whether it happens to be reachable.
    """
    settings = Settings(debug=True)
    refusal = refuse_a_shared_graph(
        settings.neo4j_uri, allow_destructive=os.environ.get(DESTRUCTIVE_OPT_IN)
    )
    if refusal is not None:
        pytest.skip(refusal)

    driver = create_driver(settings)
    try:
        await driver.verify_connectivity()
    except Exception:
        await driver.close()
        pytest.skip(f"no Neo4j at {settings.neo4j_uri} — start it with `make db-test-up`")

    await apply_schema(driver, database=settings.neo4j_database)
    await driver.execute_query(WIPE, database_=settings.neo4j_database)
    try:
        yield driver
    finally:
        await driver.execute_query(WIPE, database_=settings.neo4j_database)
        await driver.close()


@pytest.fixture
def graph_repository(engine_at_head: AsyncEngine) -> PostgresArchitectureRepository:
    return PostgresArchitectureRepository(create_session_factory(engine_at_head))


@pytest.fixture
def graph_service(graph_repository: PostgresArchitectureRepository) -> ArchitectureService:
    return ArchitectureService(graph_repository)


@pytest_asyncio.fixture
async def postgres_engine() -> AsyncIterator[AsyncEngine]:
    """An engine on the throwaway PostgreSQL, or a skip.

    This is the one door to a relational database in this suite, and every
    test behind it may drop tables, so it refuses any host that is not
    loopback, and the shared database's port, *before* connecting. Without
    that, the settings would come from `backend/.env`, which names the shared
    database — on this Mac's loopback since docs/adr/0029 — and only a fake
    password stood between this suite and `alembic downgrade base` on it.
    There is no `EA_ALLOW_DESTRUCTIVE_TESTS` on top: a local container holds
    nothing anyone would miss, so the address is the whole question.
    """
    settings = Settings(debug=True, postgres_enabled=True)
    refusal = refuse_a_shared_postgres(settings.postgres_host, settings.postgres_port)
    if refusal is not None:
        pytest.skip(refusal)

    engine = create_engine(settings)
    try:
        await check_postgres(engine)
    except RelationalStoreUnavailableError:
        await engine.dispose()
        pytest.skip(
            f"no PostgreSQL at {settings.postgres_host}:{settings.postgres_port} — "
            "start it with `make pg-up`"
        )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def alembic_config(postgres_engine: AsyncEngine) -> Config:
    """Alembic pointed at the committed `alembic.ini`, behind the guarded engine.

    It asks for `postgres_engine` although it never uses it: that dependency is
    what makes every migration in this suite pass the loopback guard, and
    `test_throwaway_guards.py` fails if it is dropped. The guard is then
    checked once more against `get_settings()`, because that — not the engine's
    settings — is what `migrations/env.py` connects with.
    """
    migrated = get_settings()
    refusal = refuse_a_shared_postgres(migrated.postgres_host, migrated.postgres_port)
    if refusal is not None:
        pytest.skip(refusal)
    return Config(Path(__file__).parents[2] / "alembic.ini")


@pytest_asyncio.fixture
async def engine_at_head(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> AsyncIterator[AsyncEngine]:
    """The throwaway database at `head`, migrated back to `base` on the way out.

    The chain rather than `create_all`: what is under test includes the
    revisions, and a schema built from the metadata would pass while the
    revision that deploys it was wrong.
    """
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    try:
        yield postgres_engine
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
