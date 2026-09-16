"""API-level checks for the health endpoint.

This is the smoke test that backs the acceptance criterion "le backend est
lancé et accessible via navigateur": if it passes, `make run-be` serves a
reachable app.

Since docs/adr/0037 it answers a second question as well — *what is missing* —
because a store that serves one section no longer stops the process, and the
only other place that says so is the log.
"""

import httpx
import pytest

from ea.main import create_app


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "degraded": []}


@pytest.mark.asyncio
async def test_openapi_schema_is_served() -> None:
    """The OpenAPI schema is the front/back contract, so it must be reachable."""
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "EA API"


@pytest.mark.asyncio
async def test_health_names_the_sections_that_will_refuse() -> None:
    """A degraded deployment is still a 200: this is liveness, not readiness.

    A 503 here would mark a container serving the catalogue perfectly well as
    unhealthy, and `backend/Dockerfile`'s `HEALTHCHECK` polls exactly this.
    """
    app = create_app()
    app.state.degraded = ("files",)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "degraded": ["files"]}
