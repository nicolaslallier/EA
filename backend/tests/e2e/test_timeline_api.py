"""The time endpoints and the `as_of` parameter, end to end against doubled stores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import an_empty_package_tree

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(service: ArchitectureService) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def create(
    client: httpx.AsyncClient, element_type: str, name: str, **properties: str
) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": element_type, "name": name, "properties": properties}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestWritingDates:
    async def test_a_patch_with_an_impossible_lifespan_is_a_typed_422(
        self, client: httpx.AsyncClient
    ) -> None:
        element = await create(client, "application_component", "Billing")

        response = await client.patch(
            f"/elements/{element['id']}",
            json={"properties": {"valid_from": "2027-06"}},
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_lifespan"
        assert "valid_from" in response.json()["detail"]
        assert "2027-06" not in response.json()["detail"]

    async def test_a_post_with_an_end_before_the_start_is_a_typed_422(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/elements",
            json={
                "element_type": "application_component",
                "name": "Billing",
                "properties": {"valid_from": "2027-02-01", "valid_until": "2027-01-01"},
            },
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_lifespan"

    async def test_a_relationship_with_a_bad_date_is_the_same_422(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await create(client, "application_component", "A")
        b = await create(client, "application_component", "B")

        response = await client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": a["id"],
                "target_id": b["id"],
                "properties": {"valid_until": "soon"},
            },
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_lifespan"
