"""Fixtures for the tests that talk to a real Neo4j.

These talk to the shared graph on the Docker cluster (docs/adr/0006).
Without a reachable database they skip rather than fail, so `uv run pytest`
stays useful off the network.

PostgreSQL is the other way round: it runs locally from `docker-compose.yml`
and holds nothing anyone would miss, so its fixture skips on an unreachable
server and destroys nothing shared.

Neo4j Community serves a single database, so there is no separate test schema to
point at and no nested transaction to roll back: isolation here means deleting
the graph between tests. That is destructive enough to require saying so out
loud, which is what `EA_ALLOW_DESTRUCTIVE_TESTS` is for — `make test-integration`
sets it, a bare `pytest` does not.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.core.config import Settings
from ea.db.neo4j import create_driver
from ea.db.postgres import RelationalStoreUnavailableError, create_engine
from ea.db.postgres import check_connectivity as check_postgres
from ea.db.schema import apply_schema
from ea.repositories.archimate_graph import Neo4jArchitectureRepository
from ea.services.architecture import ArchitectureService

WIPE = "MATCH (n:Element) DETACH DELETE n"


@pytest_asyncio.fixture
async def graph_driver() -> AsyncIterator[AsyncDriver]:
    if os.environ.get("EA_ALLOW_DESTRUCTIVE_TESTS") != "1":
        pytest.skip(
            "integration tests empty the graph; "
            "run `make test-integration`, or set EA_ALLOW_DESTRUCTIVE_TESTS=1"
        )

    settings = Settings(debug=True)
    driver = create_driver(settings)
    try:
        await driver.verify_connectivity()
    except Exception:
        await driver.close()
        pytest.skip(f"no Neo4j at {settings.neo4j_uri} — check `make db-ping`")

    await apply_schema(driver, database=settings.neo4j_database)
    await driver.execute_query(WIPE, database_=settings.neo4j_database)
    try:
        yield driver
    finally:
        await driver.execute_query(WIPE, database_=settings.neo4j_database)
        await driver.close()


@pytest.fixture
def graph_repository(graph_driver: AsyncDriver) -> Neo4jArchitectureRepository:
    return Neo4jArchitectureRepository(graph_driver, database=Settings(debug=True).neo4j_database)


@pytest.fixture
def graph_service(
    graph_repository: Neo4jArchitectureRepository,
) -> ArchitectureService:
    return ArchitectureService(graph_repository)


@pytest_asyncio.fixture
async def postgres_engine() -> AsyncIterator[AsyncEngine]:
    """An engine on the local PostgreSQL, or a skip when it is not running.

    Unlike the graph, this database is local and disposable (`make pg-up`), so
    there is nothing shared to destroy and no `EA_ALLOW_DESTRUCTIVE_TESTS`
    gate: an unreachable server is a skip, exactly like an unreachable Neo4j.
    """
    settings = Settings(debug=True, postgres_enabled=True)
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
def alembic_config() -> Config:
    """Alembic pointed at the committed `alembic.ini`, whatever the working directory."""
    return Config(Path(__file__).parents[2] / "alembic.ini")
