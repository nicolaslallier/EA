"""The architecture API, exercised end to end against a doubled graph.

These cover the contract — status codes, the error envelope, what a payload
contains — without a database. The Cypher underneath is covered separately in
`tests/integration`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService


@pytest_asyncio.fixture
async def client(service: ArchitectureService) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(debug=True), architecture_service=service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def create(client: httpx.AsyncClient, element_type: str, name: str) -> dict[str, Any]:
    response = await client.post("/elements", json={"element_type": element_type, "name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.mark.asyncio
class TestElementEndpoints:
    async def test_creating_an_element_returns_it_with_its_layer_resolved(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/elements",
            json={
                "element_type": "application_component",
                "name": "Billing",
                "properties": {"owner": "finance"},
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["layer"] == "application"
        assert body["aspect"] == "active_structure"
        assert body["properties"] == {"owner": "finance"}

    async def test_an_unknown_element_type_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/elements", json={"element_type": "microservice", "name": "Billing"}
        )

        assert response.status_code == 422

    async def test_an_unexpected_field_is_rejected_rather_than_ignored(
        self, client: httpx.AsyncClient
    ) -> None:
        """`extra="forbid"` turns a client-side typo into an error the client sees."""
        response = await client.post(
            "/elements",
            json={"element_type": "node", "name": "db-01", "critcality": "high"},
        )

        assert response.status_code == 422

    async def test_a_blank_name_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/elements", json={"element_type": "node", "name": ""})

        assert response.status_code == 422

    async def test_reading_a_missing_element_returns_the_error_envelope(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(f"/elements/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
        assert "detail" in response.json()

    async def test_the_catalogue_reports_the_total_alongside_the_page(
        self, client: httpx.AsyncClient
    ) -> None:
        await create(client, "application_component", "Billing")
        await create(client, "application_component", "Invoicing")

        response = await client.get("/elements", params={"limit": 1})

        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert len(response.json()["items"]) == 1

    async def test_an_element_can_be_renamed(self, client: httpx.AsyncClient) -> None:
        element = await create(client, "application_component", "Billing")

        response = await client.patch(f"/elements/{element['id']}", json={"name": "Invoicing"})

        assert response.status_code == 200
        assert response.json()["name"] == "Invoicing"

    async def test_the_element_type_cannot_be_changed(self, client: httpx.AsyncClient) -> None:
        """Retyping an element could invalidate links that already exist."""
        element = await create(client, "application_component", "Billing")

        response = await client.patch(f"/elements/{element['id']}", json={"element_type": "node"})

        assert response.status_code == 422

    async def test_deleting_an_element_returns_no_content(self, client: httpx.AsyncClient) -> None:
        element = await create(client, "application_component", "Billing")

        response = await client.delete(f"/elements/{element['id']}")

        assert response.status_code == 204
        assert (await client.get(f"/elements/{element['id']}")).status_code == 404


@pytest.mark.asyncio
class TestRelationshipEndpoints:
    async def test_a_legal_link_is_created(self, client: httpx.AsyncClient) -> None:
        api = await create(client, "application_service", "Invoice API")
        process = await create(client, "business_process", "Order to cash")

        response = await client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": api["id"],
                "target_id": process["id"],
            },
        )

        assert response.status_code == 201
        assert response.json()["source_type"] == "application_service"

    async def test_a_link_the_metamodel_forbids_is_a_422_with_a_reason(
        self, client: httpx.AsyncClient
    ) -> None:
        data = await create(client, "data_object", "Invoice")
        function = await create(client, "application_function", "Invoicing")

        response = await client.post(
            "/relationships",
            json={
                "relationship_type": "access",
                "source_id": data["id"],
                "target_id": function["id"],
            },
        )

        assert response.status_code == 422
        assert response.json()["error"] == "illegal_relationship"
        assert "data_object" in response.json()["detail"]

    async def test_linking_to_a_missing_element_is_a_404(self, client: httpx.AsyncClient) -> None:
        api = await create(client, "application_service", "Invoice API")

        response = await client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": api["id"],
                "target_id": str(uuid4()),
            },
        )

        assert response.status_code == 404

    async def test_a_containment_cycle_is_a_409(self, client: httpx.AsyncClient) -> None:
        outer = await create(client, "grouping", "Platform")
        inner = await create(client, "grouping", "Payments")
        await client.post(
            "/relationships",
            json={
                "relationship_type": "composition",
                "source_id": outer["id"],
                "target_id": inner["id"],
            },
        )

        response = await client.post(
            "/relationships",
            json={
                "relationship_type": "composition",
                "source_id": inner["id"],
                "target_id": outer["id"],
            },
        )

        assert response.status_code == 409
        assert response.json()["error"] == "cyclic_containment"


@pytest.mark.asyncio
class TestMetamodelEndpoints:
    async def test_the_catalogue_of_types_is_published(self, client: httpx.AsyncClient) -> None:
        """The SPA builds its palette from this instead of hard-coding 61 types."""
        response = await client.get("/metamodel")

        assert response.status_code == 200
        body = response.json()
        assert len(body["element_types"]) == 61
        assert len(body["relationship_types"]) == 11

    async def test_permitted_relationships_between_two_types_are_published(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/metamodel/relationships",
            params={"source": "application_service", "target": "business_process"},
        )

        assert response.status_code == 200
        assert "serving" in response.json()
        assert "access" not in response.json()


@pytest.mark.asyncio
async def test_the_openapi_schema_covers_the_architecture_endpoints(
    client: httpx.AsyncClient,
) -> None:
    """The schema is the front/back contract, so its shape is a test, not a hope."""
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/elements" in paths
    assert "/elements/{element_id}/impact" in paths
    assert "/metamodel" in paths


@pytest.mark.asyncio
class TestTraversalEndpoints:
    async def test_the_neighbourhood_of_an_element_is_served(
        self, client: httpx.AsyncClient
    ) -> None:
        element = await create(client, "application_component", "Billing")

        response = await client.get(f"/elements/{element['id']}/neighbourhood")

        assert response.status_code == 200
        assert response.json()["elements"][0]["name"] == "Billing"

    async def test_an_excessive_depth_is_rejected_rather_than_clamped(
        self, client: httpx.AsyncClient
    ) -> None:
        """A depth of 50 is a full graph dump; the caller should hear about it."""
        element = await create(client, "application_component", "Billing")

        response = await client.get(
            f"/elements/{element['id']}/neighbourhood", params={"depth": 50}
        )

        assert response.status_code == 422

    async def test_impact_of_a_missing_element_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"/elements/{uuid4()}/impact")

        assert response.status_code == 404


@pytest.mark.asyncio
class TestRelationshipListing:
    async def test_relationships_of_one_element_are_listed(self, client: httpx.AsyncClient) -> None:
        api = await create(client, "application_service", "Invoice API")
        process = await create(client, "business_process", "Order to cash")
        await client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": api["id"],
                "target_id": process["id"],
            },
        )

        response = await client.get("/relationships", params={"element_id": api["id"]})

        assert response.status_code == 200
        assert len(response.json()) == 1

    async def test_a_relationship_can_be_deleted(self, client: httpx.AsyncClient) -> None:
        api = await create(client, "application_service", "Invoice API")
        process = await create(client, "business_process", "Order to cash")
        created = await client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": api["id"],
                "target_id": process["id"],
            },
        )

        response = await client.delete(f"/relationships/{created.json()['id']}")

        assert response.status_code == 204

    async def test_deleting_an_unknown_relationship_is_a_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.delete(f"/relationships/{uuid4()}")

        assert response.status_code == 404
