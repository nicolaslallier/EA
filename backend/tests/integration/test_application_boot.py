"""Booting the real application against the real database.

Everywhere else the service is injected and the lifespan never runs. This is the
one place that proves the assembled process works: the pool opens, the graph is
built on it, and a request reaches PostgreSQL and comes back.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.core.config import Settings
from ea.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


async def test_the_application_boots_and_serves_the_catalogue(
    engine_at_head: AsyncEngine,
) -> None:
    """`engine_at_head` is requested for its schema and its guard, not used directly."""
    app = create_app(Settings(debug=True, postgres_enabled=True, auth_enabled=False))
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
