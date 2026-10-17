"""Template endpoints end to end: statuses, typed errors, nothing created silently."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    InMemoryDiagrams,
    InMemoryTemplates,
    StaticVerifier,
    a_reader,
    an_editor,
    an_empty_package_tree,
)
from tests.unit.test_template_service import BODY

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, diagrams: InMemoryDiagrams, templates: InMemoryTemplates
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        templates=templates,  # type: ignore[arg-type]
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def secured(
    service: ArchitectureService,
    diagrams: InMemoryDiagrams,
    templates: InMemoryTemplates,
    nobody_calling: None,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        templates=templates,  # type: ignore[arg-type]
        verifier=StaticVerifier({EDITOR: an_editor(), READER: a_reader()}),
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def a_template(
    client: httpx.AsyncClient, name: str = "Application + hôte", body: dict[str, Any] | None = None
):
    return await client.post(
        "/diagram-templates", json={"name": name, "description": "", "body": body or BODY}
    )


class TestTheLibrary:
    async def test_a_template_is_created_listed_read_renamed_and_deleted(
        self, client: httpx.AsyncClient
    ) -> None:
        created = await a_template(client)
        template_id = created.json()["id"]

        listed = (await client.get("/diagram-templates")).json()
        renamed = await client.put(f"/diagram-templates/{template_id}", json={"name": "Autre"})
        deleted = await client.delete(f"/diagram-templates/{template_id}")

        assert created.status_code == 201
        assert (
            listed[0]["slot_count"],
            listed[0]["group_count"],
            listed[0]["note_count"],
            listed[0]["link_count"],
        ) == (2, 1, 1, 1)
        assert renamed.json()["name"] == "Autre"
        assert deleted.status_code == 204
        assert (await client.get(f"/diagram-templates/{template_id}")).status_code == 404

    async def test_a_taken_name_is_a_409(self, client: httpx.AsyncClient) -> None:
        await a_template(client)

        response = await a_template(client)

        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    async def test_a_link_the_metamodel_forbids_a_bad_colour_or_too_many_slots_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        forbidden = {
            **BODY,
            "links": [
                {"source_key": "host", "target_key": "app", "relationship_type": "specialization"}
            ],
        }
        coloured = {**BODY, "groups": [{**BODY["groups"][0], "colour": 'red" onload="x'}]}
        slot = BODY["slots"][1]
        crowded = {
            **BODY,
            "slots": [{**slot, "key": f"s{i}"} for i in range(101)],
            "links": [],
            "notes": [],
        }

        results = [
            (await a_template(client, f"T{i}", body)).status_code
            for i, body in enumerate((forbidden, coloured, crowded))
        ]

        assert results == [422, 422, 422]


class TestInstantiating:
    async def test_a_diagram_is_built_with_no_relationship_unless_asked(
        self, client: httpx.AsyncClient
    ) -> None:
        template_id = (await a_template(client)).json()["id"]
        bindings = [
            {"slot_key": "app", "element_id": None, "create": {"name": "Facturation"}},
            {"slot_key": "host", "element_id": None, "create": {"name": "srv-01"}},
        ]

        plain = await client.post(
            f"/diagram-templates/{template_id}/instantiate",
            json={"name": "Sans liens", "bindings": bindings},
        )
        linked = await client.post(
            f"/diagram-templates/{template_id}/instantiate",
            json={
                "name": "Avec liens",
                "bindings": [
                    {**bindings[0], "create": {"name": "A2"}},
                    {**bindings[1], "create": {"name": "H2"}},
                ],
                "create_relationships": True,
            },
        )

        assert plain.status_code == 201 and plain.json()["relationships"] == []
        assert [r["relationship_type"] for r in linked.json()["relationships"]] == ["realization"]
        assert len(plain.json()["objects"]) == 2  # the group and the note

    async def test_an_element_of_the_wrong_type_is_a_422_and_nothing_is_created(
        self, client: httpx.AsyncClient
    ) -> None:
        template_id = (await a_template(client)).json()["id"]
        node = (await client.post("/elements", json={"element_type": "node", "name": "H"})).json()[
            "id"
        ]

        response = await client.post(
            f"/diagram-templates/{template_id}/instantiate",
            json={
                "name": "X",
                "bindings": [{"slot_key": "app", "element_id": node, "create": None}],
            },
        )

        assert response.status_code == 422
        assert (await client.get("/diagrams")).json() == []
        assert (await client.get("/elements")).json()["total"] == 1

    async def test_a_diagram_becomes_a_template(self, client: httpx.AsyncClient) -> None:
        app = (
            await client.post(
                "/elements", json={"element_type": "application_component", "name": "Facturation"}
            )
        ).json()["id"]
        diagram = (await client.post("/diagrams", json={"name": "Source"})).json()["id"]
        node = {
            "element_id": app,
            "x": 10.0,
            "y": 10.0,
            "width": 132.0,
            "height": 46.0,
            "parent_object_id": None,
            "z": 0,
        }
        await client.put(
            f"/diagrams/{diagram}/layout",
            json={"revision": 0, "nodes": [node], "objects": [], "edges": []},
        )

        response = await client.post(
            f"/diagrams/{diagram}/as-template", json={"name": "Squelette", "description": ""}
        )

        assert response.status_code == 201
        assert [s["element_type"] for s in response.json()["body"]["slots"]] == [
            "application_component"
        ]
        assert app not in response.text


class TestWhoMay:
    async def test_a_reader_lists_but_cannot_write_or_instantiate(
        self, secured: httpx.AsyncClient
    ) -> None:
        editor = {"Authorization": f"Bearer {EDITOR}"}
        reader = {"Authorization": f"Bearer {READER}"}
        template_id = (
            await secured.post(
                "/diagram-templates",
                json={"name": "T", "description": "", "body": BODY},
                headers=editor,
            )
        ).json()["id"]

        listed = await secured.get("/diagram-templates", headers=reader)
        created = await secured.post(
            "/diagram-templates",
            json={"name": "U", "description": "", "body": BODY},
            headers=reader,
        )
        built = await secured.post(
            f"/diagram-templates/{template_id}/instantiate",
            json={"name": "X", "bindings": []},
            headers=reader,
        )
        deleted = await secured.delete(f"/diagram-templates/{template_id}", headers=reader)

        assert listed.status_code == 200
        assert (created.status_code, built.status_code, deleted.status_code) == (403, 403, 403)
