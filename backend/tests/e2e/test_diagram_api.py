"""The diagram endpoints, end to end against doubled stores.

Every status code the contract of docs/adr/0031 promises, through the app a
browser talks to.
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
from tests.conftest import InMemoryDiagrams

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, diagrams: InMemoryDiagrams
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def an_element(client: httpx.AsyncClient, name: str = "Facturation") -> str:
    response = await client.post(
        "/elements", json={"element_type": "application_component", "name": name}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def a_diagram(client: httpx.AsyncClient, name: str = "Vente") -> dict[str, Any]:
    response = await client.post("/diagrams", json={"name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


def layout(*element_ids: str) -> dict[str, Any]:
    return {
        "nodes": [{"element_id": e, "x": 10.0 * i, "y": 20.0} for i, e in enumerate(element_ids)]
    }


class TestCreatingAndListing:
    async def test_a_created_diagram_is_a_201_summary(self, client: httpx.AsyncClient) -> None:
        body = await a_diagram(client)

        assert body["name"] == "Vente"
        assert body["description"] == ""
        assert body["node_count"] == 0
        assert set(body) == {"id", "name", "description", "node_count", "created_at", "updated_at"}

    async def test_a_duplicate_name_is_a_409(self, client: httpx.AsyncClient) -> None:
        await a_diagram(client)

        response = await client.post("/diagrams", json={"name": "Vente"})

        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    async def test_an_empty_name_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/diagrams", json={"name": ""})

        assert response.status_code == 422

    async def test_the_listing_is_sorted_by_name(self, client: httpx.AsyncClient) -> None:
        await a_diagram(client, "Zeta")
        await a_diagram(client, "Alpha")

        response = await client.get("/diagrams")

        assert response.status_code == 200
        assert [d["name"] for d in response.json()] == ["Alpha", "Zeta"]


class TestReading:
    async def test_a_diagram_carries_its_nodes_elements_and_relationships(
        self, client: httpx.AsyncClient
    ) -> None:
        component = await an_element(client, "Facturation")
        other = await an_element(client, "Commandes")
        linked = await client.post(
            "/relationships",
            json={"relationship_type": "flow", "source_id": component, "target_id": other},
        )
        assert linked.status_code == 201, linked.text
        diagram = await a_diagram(client)
        await client.put(f"/diagrams/{diagram['id']}/layout", json=layout(component, other))

        response = await client.get(f"/diagrams/{diagram['id']}")

        assert response.status_code == 200
        body = response.json()
        assert {n["element_id"] for n in body["nodes"]} == {component, other}
        assert {e["id"] for e in body["elements"]} == {component, other}
        assert [r["id"] for r in body["relationships"]] == [linked.json()["id"]]

    async def test_an_unknown_diagram_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"/diagrams/{uuid4()}")

        assert (response.status_code, response.json()["error"]) == (404, "not_found")


class TestUpdating:
    async def test_a_patch_answers_the_summary(self, client: httpx.AsyncClient) -> None:
        diagram = await a_diagram(client)

        response = await client.patch(f"/diagrams/{diagram['id']}", json={"description": "v2"})

        assert response.status_code == 200
        assert (response.json()["name"], response.json()["description"]) == ("Vente", "v2")

    async def test_a_patch_onto_a_taken_name_is_a_409(self, client: httpx.AsyncClient) -> None:
        await a_diagram(client, "Vente")
        other = await a_diagram(client, "Achat")

        response = await client.patch(f"/diagrams/{other['id']}", json={"name": "Vente"})

        assert response.status_code == 409

    async def test_a_patch_of_an_unknown_diagram_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.patch(f"/diagrams/{uuid4()}", json={"name": "X"})

        assert response.status_code == 404


class TestDeleting:
    async def test_a_delete_is_a_204_then_a_404(self, client: httpx.AsyncClient) -> None:
        diagram = await a_diagram(client)

        first = await client.delete(f"/diagrams/{diagram['id']}")
        second = await client.delete(f"/diagrams/{diagram['id']}")

        assert (first.status_code, second.status_code) == (204, 404)


class TestTheLayout:
    async def test_a_layout_is_a_204_and_is_read_back(self, client: httpx.AsyncClient) -> None:
        element = await an_element(client)
        diagram = await a_diagram(client)

        response = await client.put(f"/diagrams/{diagram['id']}/layout", json=layout(element))

        assert response.status_code == 204
        read = (await client.get(f"/diagrams/{diagram['id']}")).json()
        assert read["nodes"] == [{"element_id": element, "x": 0.0, "y": 20.0}]

    async def test_a_layout_for_an_unknown_diagram_is_a_404(
        self, client: httpx.AsyncClient
    ) -> None:
        element = await an_element(client)

        response = await client.put(f"/diagrams/{uuid4()}/layout", json=layout(element))

        assert response.status_code == 404

    async def test_a_layout_placing_an_unknown_element_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram = await a_diagram(client)

        response = await client.put(f"/diagrams/{diagram['id']}/layout", json=layout(str(uuid4())))

        assert (response.status_code, response.json()["error"]) == (422, "unknown_element")

    async def test_a_layout_placing_one_element_twice_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        element = await an_element(client)
        diagram = await a_diagram(client)

        response = await client.put(
            f"/diagrams/{diagram['id']}/layout", json=layout(element, element)
        )

        assert (response.status_code, response.json()["error"]) == (422, "invalid_input")

    async def test_a_coordinate_off_the_canvas_is_a_422(self, client: httpx.AsyncClient) -> None:
        element = await an_element(client)
        diagram = await a_diagram(client)

        response = await client.put(
            f"/diagrams/{diagram['id']}/layout",
            json={"nodes": [{"element_id": element, "x": 1e9, "y": 0}]},
        )

        assert response.status_code == 422

    async def test_deleting_the_element_takes_its_box_off_the_diagram(
        self, client: httpx.AsyncClient
    ) -> None:
        element = await an_element(client)
        diagram = await a_diagram(client)
        await client.put(f"/diagrams/{diagram['id']}/layout", json=layout(element))

        await client.delete(f"/elements/{element}")

        read = (await client.get(f"/diagrams/{diagram['id']}")).json()
        assert (read["nodes"], read["elements"]) == ([], [])


async def test_without_a_diagram_store_the_endpoints_fail_as_a_wiring_fault(
    service: ArchitectureService,
) -> None:
    """A shut relational store is a 500, never an empty list of diagrams."""
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/diagrams")

    assert response.status_code == 500
