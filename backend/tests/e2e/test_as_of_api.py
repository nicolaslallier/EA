"""`as_of` on the reads, end to end against doubled stores (docs/adr/0060)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryDiagrams, InMemoryRepository

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


class TestAsOfOnReads:
    async def test_without_as_of_the_catalogue_is_what_it_was(
        self, client: httpx.AsyncClient
    ) -> None:
        await create(client, "application_component", "Legacy", valid_until="2026-12-31")
        await create(client, "application_component", "Always")

        everything = (await client.get("/elements")).json()

        assert everything["total"] == 2
        assert {item["name"] for item in everything["items"]} == {"Legacy", "Always"}

    async def test_as_of_leaves_out_what_is_not_in_service_that_day(
        self, client: httpx.AsyncClient
    ) -> None:
        await create(client, "application_component", "Legacy", valid_until="2026-12-31")
        await create(client, "application_component", "Future", valid_from="2028-01-01")
        await create(client, "application_component", "Always")

        page = (await client.get("/elements", params={"as_of": "2027-06-01"})).json()

        assert [item["name"] for item in page["items"]] == ["Always"]
        assert page["total"] == 1

    async def test_the_boundary_days_are_inclusive(self, client: httpx.AsyncClient) -> None:
        await create(client, "application_component", "A", valid_until="2026-12-31")

        last = (await client.get("/elements", params={"as_of": "2026-12-31"})).json()
        after = (await client.get("/elements", params={"as_of": "2027-01-01"})).json()

        assert last["total"] == 1 and after["total"] == 0

    @pytest.mark.parametrize("bad", ["demain", "2027-06", "2027-13-40", "9" * 5000])
    async def test_an_invalid_day_is_a_422_on_every_read(
        self, client: httpx.AsyncClient, bad: str
    ) -> None:
        element = await create(client, "application_component", "A")
        for path in (
            "/elements",
            "/relationships",
            f"/elements/{element['id']}/neighbourhood",
            f"/elements/{element['id']}/impact",
        ):
            assert (await client.get(path, params={"as_of": bad})).status_code == 422, path

    async def test_the_traversals_and_links_hand_the_day_to_the_store(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        element = await create(client, "application_component", "A")

        await client.get(f"/elements/{element['id']}/neighbourhood", params={"as_of": "2028-01-01"})
        assert repository.last_as_of == date(2028, 1, 1)
        await client.get(f"/elements/{element['id']}/impact")
        assert repository.last_as_of is None
        await client.get("/relationships", params={"as_of": "2029-02-03"})
        assert repository.last_as_of == date(2029, 2, 3)


class TestDiagramsGreyInsteadOfFiltering:
    async def test_boxes_out_of_period_are_flagged_not_removed(
        self, client: httpx.AsyncClient
    ) -> None:
        legacy = await create(client, "application_component", "Legacy", valid_until="2026-12-31")
        always = await create(client, "application_component", "Always")
        link = await client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": legacy["id"],
                "target_id": always["id"],
            },
        )
        diagram = (await client.post("/diagrams", json={"name": "Vente"})).json()
        await client.put(
            f"/diagrams/{diagram['id']}/layout",
            json={
                "nodes": [
                    {"element_id": e["id"], "x": 0, "y": 0, "width": 100, "height": 50}
                    for e in (legacy, always)
                ]
            },
        )

        today = (await client.get(f"/diagrams/{diagram['id']}")).json()
        later = (
            await client.get(f"/diagrams/{diagram['id']}", params={"as_of": "2027-06-01"})
        ).json()

        assert today["inactive_element_ids"] == [] and today["inactive_relationship_ids"] == []
        assert len(later["nodes"]) == 2 and len(later["elements"]) == 2
        assert later["inactive_element_ids"] == [legacy["id"]]
        assert later["inactive_relationship_ids"] == [link.json()["id"]]
