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
from ea.db.schema import apply_schema
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
    """Boot applies the whole list every time, so a second boot must be a no-op.

    Applying it through `apply_schema` rather than statement by statement is
    the point: the second pass is the one the real server answers with "already
    exists" for every statement, so it is also what proves the session that
    carries them is one the driver accepts.
    """
    driver: AsyncDriver = graph_driver  # type: ignore[assignment]
    settings = Settings(debug=True)

    await apply_schema(driver, database=settings.neo4j_database)
    await apply_schema(driver, database=settings.neo4j_database)
