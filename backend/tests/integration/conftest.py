"""Fixtures for the tests that talk to a real Neo4j.

Run `make db-up` first. Without a reachable database these skip rather than
fail, so `uv run pytest` stays useful on a machine with no Docker.

Neo4j Community serves a single database, so there is no separate test schema to
point at and no nested transaction to roll back: isolation here means deleting
the graph between tests. That is destructive enough to require saying so out
loud, which is what `EA_ALLOW_DESTRUCTIVE_TESTS` is for — `make test-integration`
sets it, a bare `pytest` does not.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from neo4j import AsyncDriver

from ea.core.config import Settings
from ea.db.neo4j import create_driver
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
        pytest.skip(f"no Neo4j at {settings.neo4j_uri} — run `make db-up`")

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
