"""The package endpoints, end to end against doubled stores — every status of the contract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from ea.api.schemas import ROOT_PACKAGE, package_target
from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    InMemoryDiagrams,
    InMemoryPackages,
    StaticVerifier,
    a_reader,
    an_editor,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, diagrams: InMemoryDiagrams, packages: InMemoryPackages
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        packages=packages,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def a_package(
    client: httpx.AsyncClient, name: str = "Finance", parent: str | None = None
) -> dict[str, Any]:
    response = await client.post("/packages", json={"name": name, "parent_id": parent})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def an_element(client: httpx.AsyncClient, name: str = "Billing") -> str:
    response = await client.post(
        "/elements", json={"element_type": "application_component", "name": name}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


class TestCreatingAndReading:
    async def test_a_created_package_is_a_201_with_its_path_and_counts(
        self, client: httpx.AsyncClient
    ) -> None:
        finance = await a_package(client)
        budget = await a_package(client, "Budget", finance["id"])

        assert set(budget) == {
            "id", "parent_id", "name", "description", "path",
            "element_count", "diagram_count", "child_count", "created_at", "updated_at",
            "frozen_version", "frozen_by", "allowed_actions",
        }  # fmt: skip
        assert (budget["path"], budget["parent_id"]) == ("Finance / Budget", finance["id"])

    async def test_the_list_is_flat_and_counts_are_direct(self, client: httpx.AsyncClient) -> None:
        finance = await a_package(client)
        await a_package(client, "Budget", finance["id"])
        element = await an_element(client)
        await client.post(
            "/elements/move", json={"element_ids": [element], "package_id": finance["id"]}
        )

        listed = (await client.get("/packages")).json()

        assert [(p["path"], p["element_count"], p["child_count"]) for p in listed] == [
            ("Finance", 1, 1),
            ("Finance / Budget", 0, 0),
        ]

    async def test_one_package_is_read_by_id_or_is_a_404(self, client: httpx.AsyncClient) -> None:
        finance = await a_package(client)

        assert (await client.get(f"/packages/{finance['id']}")).json()["name"] == "Finance"
        missing = await client.get(f"/packages/{uuid4()}")
        assert (missing.status_code, missing.json()["error"]) == (404, "not_found")

    async def test_an_unknown_parent_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/packages", json={"name": "X", "parent_id": str(uuid4())})

        assert (response.status_code, response.json()["error"]) == (404, "not_found")

    async def test_a_taken_sibling_name_is_a_409_whatever_its_case(
        self, client: httpx.AsyncClient
    ) -> None:
        await a_package(client)

        response = await client.post("/packages", json={"name": "FINANCE"})

        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    @pytest.mark.parametrize("name", ["", "a/b"])
    async def test_a_blank_or_slashed_name_is_a_422(
        self, client: httpx.AsyncClient, name: str
    ) -> None:
        assert (await client.post("/packages", json={"name": name})).status_code == 422

    async def test_a_name_of_only_blanks_is_a_422_from_the_domain(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/packages", json={"name": "   "})

        assert (response.status_code, response.json()["error"]) == (422, "invalid_input")

    async def test_too_deep_is_a_422(self, client: httpx.AsyncClient) -> None:
        parent = None
        for level in range(12):
            parent = (await a_package(client, f"p{level}", parent))["id"]

        response = await client.post("/packages", json={"name": "trop", "parent_id": parent})

        assert (response.status_code, response.json()["error"]) == (422, "package_too_deep")


class TestUpdating:
    async def test_an_absent_parent_is_kept_and_an_explicit_null_is_the_root(
        self, client: httpx.AsyncClient
    ) -> None:
        finance = await a_package(client)
        budget = await a_package(client, "Budget", finance["id"])

        kept = await client.patch(f"/packages/{budget['id']}", json={"description": "x"})
        at_root = await client.patch(f"/packages/{budget['id']}", json={"parent_id": None})

        assert kept.json()["parent_id"] == finance["id"]
        assert at_root.json()["parent_id"] is None

    async def test_a_move_below_itself_is_a_409_cyclic_package(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client, "A")
        b = await a_package(client, "B", a["id"])

        response = await client.patch(f"/packages/{a['id']}", json={"parent_id": b["id"]})

        assert (response.status_code, response.json()["error"]) == (409, "cyclic_package")

    async def test_a_rename_onto_a_sibling_is_a_409(self, client: httpx.AsyncClient) -> None:
        await a_package(client, "A")
        b = await a_package(client, "B")

        response = await client.patch(f"/packages/{b['id']}", json={"name": "a"})

        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    async def test_an_unknown_package_is_a_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.patch(f"/packages/{uuid4()}", json={"name": "x"})).status_code == 404

    async def test_an_unknown_field_is_a_422(self, client: httpx.AsyncClient) -> None:
        a = await a_package(client)

        assert (
            await client.patch(f"/packages/{a['id']}", json={"colour": "red"})
        ).status_code == 422


class TestDeleting:
    async def test_an_empty_package_is_a_204(self, client: httpx.AsyncClient) -> None:
        a = await a_package(client)

        assert (await client.delete(f"/packages/{a['id']}")).status_code == 204
        assert (await client.get(f"/packages/{a['id']}")).status_code == 404

    async def test_a_package_that_holds_an_element_is_a_409_package_not_empty(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client)
        element = await an_element(client)
        await client.post("/elements/move", json={"element_ids": [element], "package_id": a["id"]})

        response = await client.delete(f"/packages/{a['id']}")

        assert (response.status_code, response.json()["error"]) == (409, "package_not_empty")
        assert (await client.get(f"/elements/{element}")).status_code == 200

    async def test_move_contents_to_root_moves_then_deletes(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client)
        element = await an_element(client)
        await client.post("/elements/move", json={"element_ids": [element], "package_id": a["id"]})

        response = await client.delete(f"/packages/{a['id']}", params={"move_contents_to": "root"})

        assert response.status_code == 204
        assert (await client.get(f"/elements/{element}")).json()["package_id"] is None

    async def test_move_contents_to_another_package(self, client: httpx.AsyncClient) -> None:
        a, b = await a_package(client, "A"), await a_package(client, "B")
        element = await an_element(client)
        await client.post("/elements/move", json={"element_ids": [element], "package_id": a["id"]})

        await client.delete(f"/packages/{a['id']}", params={"move_contents_to": b["id"]})

        assert (await client.get(f"/elements/{element}")).json()["package_id"] == b["id"]

    async def test_a_target_below_the_deleted_package_is_a_409_cyclic_package(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client, "A")
        b = await a_package(client, "B", a["id"])

        response = await client.delete(f"/packages/{a['id']}", params={"move_contents_to": b["id"]})

        assert (response.status_code, response.json()["error"]) == (409, "cyclic_package")

    async def test_a_target_that_is_neither_an_id_nor_root_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client)

        response = await client.delete(f"/packages/{a['id']}", params={"move_contents_to": "ici"})

        assert response.status_code == 422

    async def test_an_unknown_package_is_a_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.delete(f"/packages/{uuid4()}")).status_code == 404


class TestMovingElements:
    async def test_a_batch_is_filed_and_can_go_back_to_the_root(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client)
        one, two = await an_element(client, "one"), await an_element(client, "two")

        filed = await client.post(
            "/elements/move", json={"element_ids": [one, two], "package_id": a["id"]}
        )
        assert filed.status_code == 204
        assert (await client.get(f"/elements/{one}")).json()["package_id"] == a["id"]

        await client.post("/elements/move", json={"element_ids": [one], "package_id": None})
        assert (await client.get(f"/elements/{one}")).json()["package_id"] is None

    async def test_an_absent_element_is_a_404_and_nothing_moves(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client)
        kept = await an_element(client)

        response = await client.post(
            "/elements/move", json={"element_ids": [kept, str(uuid4())], "package_id": a["id"]}
        )

        assert (response.status_code, response.json()["error"]) == (404, "not_found")
        assert (await client.get(f"/elements/{kept}")).json()["package_id"] is None

    async def test_an_absent_package_is_a_404(self, client: httpx.AsyncClient) -> None:
        element = await an_element(client)

        response = await client.post(
            "/elements/move", json={"element_ids": [element], "package_id": str(uuid4())}
        )

        assert response.status_code == 404

    async def test_over_the_batch_limit_or_empty_is_a_422(self, client: httpx.AsyncClient) -> None:
        too_many = [str(uuid4()) for _ in range(101)]

        for ids in (too_many, []):
            response = await client.post(
                "/elements/move", json={"element_ids": ids, "package_id": None}
            )
            assert response.status_code == 422


class TestWhoMayDoWhat:
    async def test_a_reader_reads_the_tree_but_cannot_change_it(
        self, service: ArchitectureService, packages: InMemoryPackages
    ) -> None:
        verifier = StaticVerifier({"ed": an_editor(), "rd": a_reader()})
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            packages=packages,  # type: ignore[arg-type]
            verifier=verifier,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/packages", json={"name": "A"}, headers={"Authorization": "Bearer ed"}
            )
            reader = {"Authorization": "Bearer rd"}

            assert (await client.get("/packages", headers=reader)).status_code == 200
            for request in (
                client.post("/packages", json={"name": "B"}, headers=reader),
                client.patch(
                    f"/packages/{created.json()['id']}", json={"name": "C"}, headers=reader
                ),
                client.delete(f"/packages/{created.json()['id']}", headers=reader),
                client.post(
                    "/elements/move",
                    json={"element_ids": [str(uuid4())], "package_id": None},
                    headers=reader,
                ),
            ):
                response = await request
                assert (response.status_code, response.json()["error"]) == (403, "forbidden")


class TestThePackageTarget:
    async def test_root_is_none_and_an_id_is_an_id(self) -> None:
        wanted = uuid4()

        assert package_target(ROOT_PACKAGE) is None
        assert package_target(str(wanted)) == wanted
        assert isinstance(package_target(str(wanted)), UUID)

    async def test_anything_else_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match="root"):
            package_target("ici")
