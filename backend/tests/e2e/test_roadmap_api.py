"""The plateau, gap and roadmap endpoints, seen from outside (docs/adr/0061)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository, StaticVerifier, a_reader, an_editor


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, repository: InMemoryRepository
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False), architecture_service=service, timeline=repository
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


async def contain(client: httpx.AsyncClient, owner: dict[str, Any], member: dict[str, Any]) -> None:
    response = await client.post(
        "/relationships",
        json={
            "relationship_type": "composition",
            "source_id": owner["id"],
            "target_id": member["id"],
        },
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
class TestPlateaus:
    async def test_they_are_listed_earliest_first_with_their_size(
        self, client: httpx.AsyncClient
    ) -> None:
        await create(client, "plateau", "To-Be", valid_from="2028-01-01")
        as_is = await create(client, "plateau", "As-Is", valid_from="2024-01-01")
        await contain(client, as_is, await create(client, "application_component", "Billing"))

        response = await client.get("/plateaus")

        assert response.status_code == 200
        assert [(p["name"], p["valid_from"], p["member_count"]) for p in response.json()] == [
            ("As-Is", "2024-01-01", 1),
            ("To-Be", "2028-01-01", 0),
        ]


@pytest.mark.asyncio
class TestTheGap:
    async def test_the_difference_between_two_plateaus(self, client: httpx.AsyncClient) -> None:
        a, b = await create(client, "plateau", "A"), await create(client, "plateau", "B")
        old = await create(client, "application_component", "Old")
        new = await create(client, "application_component", "New")
        await contain(client, a, old)
        await contain(client, b, new)

        response = await client.get("/plateaus/gap", params={"a": a["id"], "b": b["id"]})

        body = response.json()
        assert response.status_code == 200
        assert [e["name"] for e in body["only_in_a"]] == ["Old"]
        assert [e["name"] for e in body["only_in_b"]] == ["New"]
        assert body["common"] == [] and body["gaps"] == []

    async def test_comparing_a_plateau_with_itself_is_a_typed_422(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await create(client, "plateau", "A")

        response = await client.get("/plateaus/gap", params={"a": a["id"], "b": a["id"]})

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_timeline"

    async def test_something_that_is_not_a_plateau_is_a_typed_422(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await create(client, "plateau", "A")
        app = await create(client, "application_component", "App")

        response = await client.get("/plateaus/gap", params={"a": a["id"], "b": app["id"]})

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_timeline"

    async def test_an_unknown_plateau_is_a_404(self, client: httpx.AsyncClient) -> None:
        a = await create(client, "plateau", "A")

        response = await client.get(
            "/plateaus/gap",
            params={"a": a["id"], "b": "00000000-0000-4000-8000-000000000000"},
        )

        assert response.status_code == 404

    async def test_creating_the_gap_is_a_201_and_the_second_is_a_409(
        self, client: httpx.AsyncClient
    ) -> None:
        a, b = await create(client, "plateau", "A"), await create(client, "plateau", "B")

        created = await client.post("/plateaus/gap", json={"a": a["id"], "b": b["id"]})
        again = await client.post("/plateaus/gap", json={"a": b["id"], "b": a["id"]})
        compared = (await client.get("/plateaus/gap", params={"a": a["id"], "b": b["id"]})).json()

        assert created.status_code == 201
        assert created.json()["element_type"] == "gap"
        assert again.status_code == 409
        assert again.json()["error"] == "duplicate_gap"
        assert [g["id"] for g in compared["gaps"]] == [created.json()["id"]]

    async def test_a_body_with_a_stray_field_is_refused(self, client: httpx.AsyncClient) -> None:
        a, b = await create(client, "plateau", "A"), await create(client, "plateau", "B")

        response = await client.post(
            "/plateaus/gap", json={"a": a["id"], "b": b["id"], "owner": "me"}
        )

        assert response.status_code == 422


@pytest.mark.asyncio
class TestTheRoadmap:
    async def test_items_with_their_dates_and_links(self, client: httpx.AsyncClient) -> None:
        to_be = await create(client, "plateau", "To-Be", valid_from="2028-01-01")
        wp = await create(
            client, "work_package", "Migration", valid_from="2026-06-01", valid_until="2027-12-31"
        )
        deliverable = await create(client, "deliverable", "Runbook")
        await contain(client, to_be, wp)
        await client.post(
            "/relationships",
            json={
                "relationship_type": "realization",
                "source_id": wp["id"],
                "target_id": deliverable["id"],
            },
        )

        response = await client.get("/roadmap")

        body = response.json()
        assert response.status_code == 200
        assert (body["window_from"], body["window_to"]) == ("2026-06-01", "2028-01-01")
        items = {i["name"]: i for i in body["items"]}
        assert items["Migration"]["plateau_ids"] == [to_be["id"]]
        assert items["Runbook"]["dated"] is False
        assert [(e["relationship_type"]) for e in body["edges"]] == ["realization"]

    async def test_a_window_is_read_from_and_to(self, client: httpx.AsyncClient) -> None:
        await create(client, "work_package", "Early", valid_until="2025-12-31")
        await create(client, "work_package", "Inside", valid_from="2026-03-01")

        response = await client.get("/roadmap", params={"from": "2026-01-01", "to": "2026-12-31"})

        assert [i["name"] for i in response.json()["items"]] == ["Inside"]

    async def test_a_window_wider_than_twenty_years_is_a_typed_422(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/roadmap", params={"from": "2000-01-01", "to": "2030-01-01"})

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_timeline"

    @pytest.mark.parametrize("bad", ["demain", "2026-13-01", "2026-06"])
    async def test_an_invalid_day_is_a_422(self, client: httpx.AsyncClient, bad: str) -> None:
        assert (await client.get("/roadmap", params={"from": bad})).status_code == 422


@pytest.mark.asyncio
class TestWhoMayCallWhat:
    async def _app(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> httpx.AsyncClient:
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            timeline=repository,
            verifier=StaticVerifier({"reader": a_reader(), "editor": an_editor()}),
        )
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def test_every_endpoint_wants_a_token(
        self, service: ArchitectureService, repository: InMemoryRepository, nobody_calling: None
    ) -> None:
        async with await self._app(service, repository) as anonymous:
            assert (await anonymous.get("/plateaus")).status_code == 401
            assert (await anonymous.get("/roadmap")).status_code == 401
            assert (await anonymous.get("/plateaus/gap")).status_code == 401
            assert (await anonymous.post("/plateaus/gap", json={})).status_code == 401

    async def test_a_reader_reads_and_cannot_draw_a_gap(
        self, service: ArchitectureService, repository: InMemoryRepository, nobody_calling: None
    ) -> None:
        reader = {"Authorization": "Bearer reader"}
        editor = {"Authorization": "Bearer editor"}
        async with await self._app(service, repository) as client:
            a = (
                await client.post(
                    "/elements", json={"element_type": "plateau", "name": "A"}, headers=editor
                )
            ).json()
            b = (
                await client.post(
                    "/elements", json={"element_type": "plateau", "name": "B"}, headers=editor
                )
            ).json()

            assert (await client.get("/plateaus", headers=reader)).status_code == 200
            assert (await client.get("/roadmap", headers=reader)).status_code == 200
            refused = await client.post(
                "/plateaus/gap", json={"a": a["id"], "b": b["id"]}, headers=reader
            )
            drawn = await client.post(
                "/plateaus/gap", json={"a": a["id"], "b": b["id"]}, headers=editor
            )

        assert refused.status_code == 403
        assert drawn.status_code == 201
