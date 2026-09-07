"""Booting the real application against the real graph.

Everywhere else the service is injected and the lifespan never runs. This is the
one place that proves the assembled process works: the driver opens, the schema
is applied to whatever state the database is in, and a request reaches Neo4j and
comes back. It is the test that would have caught a bad Bolt URI or a schema
statement Neo4j refuses.
"""

from __future__ import annotations

import httpx
import pytest
from neo4j import AsyncDriver

from ea.core.config import Settings
from ea.db.schema import SCHEMA_STATEMENTS
from ea.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_the_application_boots_against_the_graph_and_serves_a_request(
    graph_driver: object,
) -> None:
    """`graph_driver` is only requested to skip when there is no database."""
    app = create_app(Settings(debug=True))
    transport = httpx.ASGITransport(app=app)

    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        created = await client.post(
            "/elements", json={"element_type": "node", "name": "boot-check-01"}
        )
        listed = await client.get("/elements", params={"search": "boot-check"})

    assert created.status_code == 201
    assert [item["name"] for item in listed.json()["items"]] == ["boot-check-01"]


async def test_applying_the_schema_twice_changes_nothing(
    graph_driver: object,
) -> None:
    """Boot applies the whole list every time, so a second boot must be a no-op."""
    driver: AsyncDriver = graph_driver  # type: ignore[assignment]
    settings = Settings(debug=True)

    for statement in SCHEMA_STATEMENTS:
        await driver.execute_query(statement, database_=settings.neo4j_database)
        await driver.execute_query(statement, database_=settings.neo4j_database)
