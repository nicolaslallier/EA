"""API-level checks for the health endpoint.

This is the smoke test that backs the acceptance criterion "le backend est
lancé et accessible via navigateur": if it passes, `make run-be` serves a
reachable app.
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
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_openapi_schema_is_served() -> None:
    """The OpenAPI schema is the front/back contract, so it must be reachable."""
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "EA API"
