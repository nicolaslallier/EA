"""Profile endpoints end to end: statuses, typed errors, the round trip of a document."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    InMemoryProfiles,
    StaticVerifier,
    a_reader,
    an_editor,
    an_empty_package_tree,
)

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, profiles: InMemoryProfiles
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        profiles=profiles,  # type: ignore[arg-type]
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def secured(
    service: ArchitectureService, profiles: InMemoryProfiles, nobody_calling: None
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        profiles=profiles,  # type: ignore[arg-type]
        verifier=StaticVerifier({EDITOR: an_editor(), READER: a_reader()}),
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def stereotype(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "database",
        "base_type": "application_component",
        "colour": "#ffe0e0",
        "icon_key": "database",
        "properties": [
            {
                "key": "tier",
                "label": "Niveau",
                "required": True,
                "kind": "enum",
                "allowed_values": ["1", "2"],
                "template": None,
                "help": "",
            },
        ],
        **overrides,
    }


async def a_profile(client: httpx.AsyncClient, name: str = "SI") -> dict[str, Any]:
    response = await client.post("/profiles", json={"name": name, "description": ""})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def an_element(client: httpx.AsyncClient, element_type: str = "application_component") -> str:
    response = await client.post(
        "/elements", json={"element_type": element_type, "name": "Facturation"}
    )
    return str(response.json()["id"])


class TestProfilesAndStereotypes:
    async def test_a_profile_is_created_read_and_listed_with_its_stereotypes(
        self, client: httpx.AsyncClient
    ) -> None:
        profile = await a_profile(client)

        created = await client.post(f"/profiles/{profile['id']}/stereotypes", json=stereotype())
        read = (await client.get(f"/profiles/{profile['id']}")).json()
        flat = (await client.get("/stereotypes")).json()

        assert created.status_code == 201
        assert [s["name"] for s in read["stereotypes"]] == ["database"]
        assert [s["id"] for s in flat] == [created.json()["id"]]

    async def test_a_taken_profile_name_is_a_409_duplicate(self, client: httpx.AsyncClient) -> None:
        await a_profile(client)

        response = await client.post("/profiles", json={"name": "SI", "description": ""})

        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    async def test_an_empty_template_or_a_reserved_key_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        profile = await a_profile(client)
        url = f"/profiles/{profile['id']}/stereotypes"
        prop = {"label": "X", "required": False, "allowed_values": [], "help": ""}

        empty_template = await client.post(
            url,
            json=stereotype(properties=[{**prop, "key": "k", "kind": "template", "template": ""}]),
        )
        reserved = await client.post(
            url,
            json=stereotype(
                properties=[{**prop, "key": "ip_address", "kind": "free", "template": None}]
            ),
        )
        colour = await client.post(url, json=stereotype(colour='red" onload="x'))

        assert (empty_template.status_code, reserved.status_code, colour.status_code) == (
            422,
            422,
            422,
        )

    async def test_an_icon_outside_the_closed_set_is_a_422(self, client: httpx.AsyncClient) -> None:
        profile = await a_profile(client)

        response = await client.post(
            f"/profiles/{profile['id']}/stereotypes", json=stereotype(icon_key="skull")
        )

        assert response.status_code == 422


class TestOnElements:
    async def test_applying_shows_the_stereotype_on_the_element_everywhere(
        self, client: httpx.AsyncClient
    ) -> None:
        profile = await a_profile(client)
        sid = (
            await client.post(f"/profiles/{profile['id']}/stereotypes", json=stereotype())
        ).json()["id"]
        element = await an_element(client)

        applied = await client.put(
            f"/elements/{element}/stereotypes", json={"stereotype_ids": [sid]}
        )

        assert applied.status_code == 200
        assert [s["name"] for s in applied.json()["stereotypes"]] == ["database"]
        assert [
            s["name"] for s in (await client.get(f"/elements/{element}")).json()["stereotypes"]
        ] == ["database"]
        page = (await client.get("/elements")).json()
        assert [s["name"] for s in page["items"][0]["stereotypes"]] == ["database"]

    async def test_a_stereotype_of_another_type_is_a_422_naming_both(
        self, client: httpx.AsyncClient
    ) -> None:
        profile = await a_profile(client)
        sid = (
            await client.post(f"/profiles/{profile['id']}/stereotypes", json=stereotype())
        ).json()["id"]
        node = await an_element(client, "node")

        response = await client.put(f"/elements/{node}/stereotypes", json={"stereotype_ids": [sid]})

        assert response.status_code == 422
        assert (
            "application_component" in response.json()["detail"]
            and "node" in response.json()["detail"]
        )

    async def test_patching_a_property_against_a_carried_stereotype_is_checked(
        self, client: httpx.AsyncClient
    ) -> None:
        profile = await a_profile(client)
        sid = (
            await client.post(f"/profiles/{profile['id']}/stereotypes", json=stereotype())
        ).json()["id"]
        element = await an_element(client)
        await client.put(f"/elements/{element}/stereotypes", json={"stereotype_ids": [sid]})

        bad = await client.patch(f"/elements/{element}", json={"properties": {"tier": "9"}})
        good = await client.patch(f"/elements/{element}", json={"properties": {"tier": "2"}})

        assert (bad.status_code, good.status_code) == (422, 200)
        assert [s["name"] for s in good.json()["stereotypes"]] == ["database"]

    async def test_deleting_a_stereotype_in_use_is_a_409_with_the_count(
        self, client: httpx.AsyncClient
    ) -> None:
        profile = await a_profile(client)
        sid = (
            await client.post(f"/profiles/{profile['id']}/stereotypes", json=stereotype())
        ).json()["id"]
        element = await an_element(client)
        await client.put(f"/elements/{element}/stereotypes", json={"stereotype_ids": [sid]})

        response = await client.delete(f"/stereotypes/{sid}")

        assert (response.status_code, response.json()["error"]) == (409, "stereotype_in_use")
        assert "1 element" in response.json()["detail"]


class TestExportAndImport:
    async def test_a_profile_goes_out_and_comes_back_in_under_another_name(
        self, client: httpx.AsyncClient
    ) -> None:
        profile = await a_profile(client)
        await client.post(f"/profiles/{profile['id']}/stereotypes", json=stereotype())

        document = (await client.get(f"/profiles/{profile['id']}/export")).json()
        imported = await client.post(
            "/profiles/import",
            content=json.dumps({**document, "name": "SI (copie)"}),
            headers={"content-type": "application/json"},
        )

        assert document["profile_format"] == 1
        assert imported.status_code == 201
        assert imported.json()["stereotypes"][0]["properties"][0]["key"] == "tier"

    async def test_a_document_of_another_format_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/profiles/import",
            content=json.dumps(
                {"profile_format": 2, "name": "X", "description": "", "stereotypes": []}
            ),
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 422

    async def test_a_document_over_256_kb_is_refused_before_it_is_read(
        self, client: httpx.AsyncClient
    ) -> None:
        big = json.dumps(
            {"profile_format": 1, "name": "X", "description": "x" * 300_000, "stereotypes": []}
        )

        response = await client.post(
            "/profiles/import", content=big, headers={"content-type": "application/json"}
        )

        assert response.status_code == 422
        assert "256" in response.json()["detail"]

    async def test_malformed_json_is_a_422_that_leaks_nothing(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/profiles/import", content="{not json", headers={"content-type": "application/json"}
        )

        assert response.status_code == 422
        assert "pydantic" not in response.text.lower()


class TestWhoMayWrite:
    async def test_a_reader_reads_profiles_and_cannot_write_or_import(
        self, secured: httpx.AsyncClient
    ) -> None:
        editor = {"Authorization": f"Bearer {EDITOR}"}
        reader = {"Authorization": f"Bearer {READER}"}
        created = await secured.post(
            "/profiles", json={"name": "SI", "description": ""}, headers=editor
        )
        pid = created.json()["id"]

        listed = await secured.get("/profiles", headers=reader)
        created_by_reader = await secured.post(
            "/profiles", json={"name": "X", "description": ""}, headers=reader
        )
        imported = await secured.post(
            "/profiles/import",
            content=json.dumps(
                {"profile_format": 1, "name": "Y", "description": "", "stereotypes": []}
            ),
            headers={**reader, "content-type": "application/json"},
        )
        deleted = await secured.delete(f"/profiles/{pid}", headers=reader)

        assert listed.status_code == 200
        assert (created_by_reader.status_code, imported.status_code, deleted.status_code) == (
            403,
            403,
            403,
        )
