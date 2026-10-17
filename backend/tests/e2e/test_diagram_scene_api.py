"""The scene contract of `PUT /diagrams/{id}/layout`, end to end: revisions, groups, routes."""

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
from tests.conftest import (
    InMemoryDiagrams,
    StaticVerifier,
    a_reader,
    an_editor,
    an_empty_package_tree,
)

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, diagrams: InMemoryDiagrams
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def secured(
    service: ArchitectureService, diagrams: InMemoryDiagrams, nobody_calling: None
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        verifier=StaticVerifier({EDITOR: an_editor(), READER: a_reader()}),
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def an_element(client: httpx.AsyncClient, name: str = "Facturation") -> str:
    response = await client.post(
        "/elements", json={"element_type": "application_component", "name": name}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def a_diagram(client: httpx.AsyncClient, name: str = "Vente") -> str:
    response = await client.post("/diagrams", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def box(element: str, **overrides: Any) -> dict[str, Any]:
    return {
        "element_id": element,
        "x": 10.0,
        "y": 20.0,
        "width": 200.0,
        "height": 80.0,
        "parent_object_id": None,
        "z": 0,
        **overrides,
    }


def group(**overrides: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "kind": "group",
        "x": 0.0,
        "y": 0.0,
        "width": 300.0,
        "height": 200.0,
        "text": "Domaine Paiement",
        "colour": None,
        "z": 0,
        "parent_id": None,
        "anchor_element_id": None,
        **overrides,
    }


def scene(
    revision: int = 0, nodes: list[dict[str, Any]] | None = None, **rest: Any
) -> dict[str, Any]:
    return {"revision": revision, "nodes": nodes or [], "objects": [], "edges": [], **rest}


class TestTheRevision:
    async def test_a_save_returns_the_new_revision_in_a_header(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram = await a_diagram(client)

        first = await client.put(f"/diagrams/{diagram}/layout", json=scene(0))
        second = await client.put(f"/diagrams/{diagram}/layout", json=scene(1))

        assert (first.status_code, first.headers["x-diagram-revision"]) == (204, "1")
        assert (second.status_code, second.headers["x-diagram-revision"]) == (204, "2")
        assert (await client.get(f"/diagrams/{diagram}")).json()["revision"] == 2

    async def test_two_editors_on_one_revision_the_second_gets_a_409_and_the_first_survives(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram = await a_diagram(client)
        winner = await an_element(client, "Gagnant")
        loser = await an_element(client, "Perdant")
        assert (
            await client.put(f"/diagrams/{diagram}/layout", json=scene(0, [box(winner)]))
        ).status_code == 204

        stale = await client.put(f"/diagrams/{diagram}/layout", json=scene(0, [box(loser)]))

        assert stale.status_code == 409
        assert stale.json()["error"] == "diagram_revision_conflict"
        assert "revision 1" in stale.json()["detail"]
        read = (await client.get(f"/diagrams/{diagram}")).json()
        assert [n["element_id"] for n in read["nodes"]] == [winner]
        assert read["revision"] == 1
        # The loser reloads, sees revision 1, and can save again.
        retry = await client.put(f"/diagrams/{diagram}/layout", json=scene(1, [box(loser)]))
        assert retry.status_code == 204

    async def test_a_stale_save_naming_a_deleted_element_is_a_409_not_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram = await a_diagram(client)
        gone = await an_element(client, "Parti")
        assert (
            await client.put(f"/diagrams/{diagram}/layout", json=scene(0, [box(gone)]))
        ).status_code == 204
        assert (await client.put(f"/diagrams/{diagram}/layout", json=scene(1))).status_code == 204
        assert (await client.delete(f"/elements/{gone}")).status_code == 204

        stale = await client.put(f"/diagrams/{diagram}/layout", json=scene(1, [box(gone)]))

        assert stale.status_code == 409
        assert stale.json()["error"] == "diagram_revision_conflict"

    async def test_a_body_without_a_revision_is_a_422(self, client: httpx.AsyncClient) -> None:
        diagram = await a_diagram(client)
        body = scene(0)
        del body["revision"]

        response = await client.put(f"/diagrams/{diagram}/layout", json=body)

        assert response.status_code == 422

    async def test_the_header_is_exposed_to_a_browser(self, client: httpx.AsyncClient) -> None:
        diagram = await a_diagram(client)

        response = await client.put(
            f"/diagrams/{diagram}/layout",
            json=scene(0),
            headers={"Origin": "http://localhost:5173"},
        )

        assert "x-diagram-revision" in response.headers["access-control-expose-headers"].lower()


class TestTheScene:
    async def test_notes_groups_and_bends_round_trip(self, client: httpx.AsyncClient) -> None:
        diagram = await a_diagram(client)
        source = await an_element(client, "A")
        target = await an_element(client, "B")
        link = (
            await client.post(
                "/relationships",
                json={"relationship_type": "association", "source_id": source, "target_id": target},
            )
        ).json()["id"]
        outer = group()
        note = group(kind="note", text="Voir ADR", anchor_element_id=source, colour="#ffe0e0")
        body = scene(
            0,
            [box(source, parent_object_id=outer["id"]), box(target, x=400.0)],
            objects=[outer, note],
            edges=[{"relationship_id": link, "waypoints": [{"x": 100.0, "y": 80.0}]}],
        )

        assert (await client.put(f"/diagrams/{diagram}/layout", json=body)).status_code == 204

        read = (await client.get(f"/diagrams/{diagram}")).json()
        assert {o["id"] for o in read["objects"]} == {outer["id"], note["id"]}
        assert read["edges"] == [{"relationship_id": link, "waypoints": [{"x": 100.0, "y": 80.0}]}]
        assert {n["element_id"]: n["parent_object_id"] for n in read["nodes"]}[source] == outer[
            "id"
        ]

    async def test_an_empty_group_is_accepted(self, client: httpx.AsyncClient) -> None:
        diagram = await a_diagram(client)

        response = await client.put(f"/diagrams/{diagram}/layout", json=scene(0, objects=[group()]))

        assert response.status_code == 204

    async def test_groups_that_contain_each_other_are_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram = await a_diagram(client)
        first, second = group(), group()
        first["parent_id"], second["parent_id"] = second["id"], first["id"]

        response = await client.put(
            f"/diagrams/{diagram}/layout", json=scene(0, objects=[first, second])
        )

        assert (response.status_code, response.json()["error"]) == (422, "invalid_input")

    async def test_a_sixth_level_of_groups_is_a_422(self, client: httpx.AsyncClient) -> None:
        diagram = await a_diagram(client)
        chain: list[dict[str, Any]] = []
        for _ in range(6):
            chain.append(group(parent_id=chain[-1]["id"] if chain else None))

        response = await client.put(f"/diagrams/{diagram}/layout", json=scene(0, objects=chain))

        assert response.status_code == 422

    async def test_a_route_for_a_link_not_between_two_placed_elements_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram = await a_diagram(client)
        source = await an_element(client, "A")
        target = await an_element(client, "B")
        link = (
            await client.post(
                "/relationships",
                json={"relationship_type": "association", "source_id": source, "target_id": target},
            )
        ).json()["id"]

        response = await client.put(
            f"/diagrams/{diagram}/layout",
            json=scene(0, [box(source)], edges=[{"relationship_id": link, "waypoints": []}]),
        )

        assert response.status_code == 422

    async def test_too_many_bends_or_a_bad_colour_never_reach_the_domain(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram = await a_diagram(client)

        bends = await client.put(
            f"/diagrams/{diagram}/layout",
            json=scene(
                0, edges=[{"relationship_id": str(uuid4()), "waypoints": [{"x": 0, "y": 0}] * 21}]
            ),
        )
        colour = await client.put(
            f"/diagrams/{diagram}/layout",
            json=scene(0, objects=[group(colour='red" onload="x')]),
        )

        assert (bends.status_code, colour.status_code) == (422, 422)

    async def test_deleting_an_element_leaves_the_group_and_its_other_children(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram = await a_diagram(client)
        doomed = await an_element(client, "Doomed")
        sibling = await an_element(client, "Sibling")
        outer = group()
        await client.put(
            f"/diagrams/{diagram}/layout",
            json=scene(
                0,
                [
                    box(doomed, parent_object_id=outer["id"]),
                    box(sibling, parent_object_id=outer["id"], x=300.0),
                ],
                objects=[outer],
            ),
        )

        await client.delete(f"/elements/{doomed}")

        read = (await client.get(f"/diagrams/{diagram}")).json()
        assert [n["element_id"] for n in read["nodes"]] == [sibling]
        assert [o["id"] for o in read["objects"]] == [outer["id"]]


class TestWhoMaySave:
    async def test_a_reader_reads_the_scene_but_cannot_save_it(
        self, secured: httpx.AsyncClient
    ) -> None:
        editor = {"Authorization": f"Bearer {EDITOR}"}
        reader = {"Authorization": f"Bearer {READER}"}
        created = await secured.post("/diagrams", json={"name": "Vente"}, headers=editor)
        diagram = created.json()["id"]

        read = await secured.get(f"/diagrams/{diagram}", headers=reader)
        write = await secured.put(f"/diagrams/{diagram}/layout", json=scene(0), headers=reader)

        assert (read.status_code, write.status_code) == (200, 403)

    async def test_nobody_saves_without_a_token(self, secured: httpx.AsyncClient) -> None:
        response = await secured.put(f"/diagrams/{uuid4()}/layout", json=scene(0))

        assert response.status_code == 401
