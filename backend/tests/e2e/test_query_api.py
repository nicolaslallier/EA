"""`POST /elements/search` — the contract, against a doubled graph.

The double does not filter (tests/conftest.py), so what is proved here is the
door: the body, the page, the refusals. What the SQL does with a query is
`tests/integration/test_element_search.py`'s.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.archimate import ElementType
from ea.domain.packages import Package
from ea.domain.query import ElementQuery, to_json
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    FIXED_NOW,
    InMemoryPackages,
    InMemorySavedQueries,
    StaticVerifier,
    a_reader,
    an_editor,
    an_empty_package_tree,
)

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


def body(**changes: Any) -> dict[str, Any]:
    return {**to_json(ElementQuery()), **changes}


async def test_a_query_answers_a_page_of_elements(
    client: httpx.AsyncClient, service: ArchitectureService
) -> None:
    await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name="Billing")

    response = await client.post("/elements/search", json=body(limit=10, offset=0))

    assert response.status_code == 200
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (1, 10, 0)
    assert page["items"][0]["name"] == "Billing"
    assert "package_id" in page["items"][0]


@pytest.mark.parametrize(
    "changes",
    [
        {"properties": [{"key": "a", "op": "gt", "value": "1"}]},
        {"properties": [{"key": "a-b", "op": "exists", "value": None}]},
        {"element_types": ["flux_capacitor"]},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"version": 2},
        {"colour": "red"},
    ],
)
async def test_an_invalid_body_is_a_422(client: httpx.AsyncClient, changes: dict[str, Any]) -> None:
    assert (await client.post("/elements/search", json=body(**changes))).status_code == 422


async def test_a_missing_field_is_a_422(client: httpx.AsyncClient) -> None:
    incomplete = body()
    del incomplete["layers"]

    assert (await client.post("/elements/search", json=incomplete)).status_code == 422


async def test_the_root_and_a_package_at_once_is_a_422_from_the_domain(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/elements/search", json=body(root_only=True, package_id=str(uuid4()))
    )

    assert (response.status_code, response.json()["error"]) == (422, "invalid_input")


async def test_a_property_test_that_lacks_its_value_is_a_422_from_the_domain(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/elements/search",
        json=body(properties=[{"key": "owner", "op": "eq", "value": None}]),
    )

    assert (response.status_code, response.json()["error"]) == (422, "invalid_input")


async def test_a_reader_may_search_and_nobody_may_search_without_a_token(
    service: ArchitectureService, nobody_calling: None
) -> None:
    verifier = StaticVerifier({"ed": an_editor(), "rd": a_reader()})
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        verifier=verifier,
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        as_reader = await client.post(
            "/elements/search", json=body(), headers={"Authorization": "Bearer rd"}
        )
        anonymous = await client.post("/elements/search", json=body())

    assert as_reader.status_code == 200
    assert anonymous.status_code == 401


@pytest_asyncio.fixture
async def saved_client(
    service: ArchitectureService,
    packages: InMemoryPackages,
    saved_queries: InMemorySavedQueries,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        packages=packages,  # type: ignore[arg-type]
        queries=saved_queries,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def save(client: httpx.AsyncClient, name: str = "Les noeuds", **query: Any) -> dict[str, Any]:
    response = await client.post(
        "/queries", json={"name": name, "description": "d", "query": body(**query)}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestSavingAndReading:
    async def test_a_saved_query_is_a_201_with_its_author_and_its_query(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        created = await save(saved_client, text="db")

        assert created["created_by"] == "local-developer"
        assert created["query"]["text"] == "db"
        assert set(created) == {
            "id", "name", "description", "created_by", "created_at", "updated_at", "query",
        }  # fmt: skip

    async def test_the_author_cannot_be_written_from_the_body(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        response = await saved_client.post(
            "/queries",
            json={"name": "A", "created_by": "mallory", "query": body()},
        )

        assert response.status_code == 422

    async def test_a_taken_name_is_a_409(self, saved_client: httpx.AsyncClient) -> None:
        await save(saved_client, "A")

        response = await saved_client.post("/queries", json={"name": "A", "query": body()})

        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    async def test_a_contradictory_query_is_refused_at_the_door(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        response = await saved_client.post(
            "/queries",
            json={"name": "A", "query": body(root_only=True, package_id=str(uuid4()))},
        )

        assert (response.status_code, response.json()["error"]) == (422, "invalid_input")

    async def test_the_list_is_summaries_without_the_query(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        await save(saved_client, "beta")
        await save(saved_client, "Alpha")

        listed = (await saved_client.get("/queries")).json()

        assert [q["name"] for q in listed] == ["Alpha", "beta"]
        assert all("query" not in q for q in listed)

    async def test_one_query_comes_back_as_it_was_sent(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        created = await save(saved_client, limit=10, order="created_at")

        read = (await saved_client.get(f"/queries/{created['id']}")).json()

        assert read["query"] == body(limit=10, order="created_at")

    async def test_an_unknown_id_is_a_404(self, saved_client: httpx.AsyncClient) -> None:
        for request in (
            saved_client.get(f"/queries/{uuid4()}"),
            saved_client.patch(f"/queries/{uuid4()}", json={"name": "x"}),
            saved_client.delete(f"/queries/{uuid4()}"),
            saved_client.get(f"/queries/{uuid4()}/elements"),
        ):
            assert (await request).status_code == 404


class TestChangingAndDeleting:
    async def test_patch_renames_and_replaces_the_query_and_keeps_the_rest(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        created = await save(saved_client, "A", text="db")

        renamed = await saved_client.patch(f"/queries/{created['id']}", json={"name": "B"})
        replaced = await saved_client.patch(
            f"/queries/{created['id']}", json={"query": body(text="neuf")}
        )

        assert renamed.json()["query"]["text"] == "db"
        assert (replaced.json()["name"], replaced.json()["query"]["text"]) == ("B", "neuf")
        assert replaced.json()["created_by"] == "local-developer"

    async def test_delete_is_a_204_and_the_elements_it_found_stay(
        self, saved_client: httpx.AsyncClient, service: ArchitectureService
    ) -> None:
        element = await service.create_element(element_type=ElementType.NODE, name="db-01")
        created = await save(saved_client)

        assert (await saved_client.delete(f"/queries/{created['id']}")).status_code == 204
        assert (await saved_client.get(f"/queries/{created['id']}")).status_code == 404
        assert (await service.get_element(element.id)).name == "db-01"


class TestRunning:
    async def test_a_run_is_a_page_of_elements(
        self, saved_client: httpx.AsyncClient, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=ElementType.NODE, name="a")
        await service.create_element(element_type=ElementType.NODE, name="b")
        created = await save(saved_client)

        page = (
            await saved_client.get(f"/queries/{created['id']}/elements", params={"limit": 1})
        ).json()

        assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (2, 1, 0, 1)

    async def test_a_package_that_is_gone_is_a_422_unknown_package_and_the_query_stays(
        self, saved_client: httpx.AsyncClient, packages: InMemoryPackages
    ) -> None:
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW), audit=())
        created = await save(saved_client, package_id=str(finance.id))
        await packages.delete(finance.id, into=None, audit=())

        response = await saved_client.get(f"/queries/{created['id']}/elements")

        assert (response.status_code, response.json()["error"]) == (422, "unknown_package")
        assert (await saved_client.get(f"/queries/{created['id']}")).status_code == 200

    async def test_a_stored_query_that_cannot_be_read_is_a_422_invalid_query_and_the_list_survives(
        self, saved_client: httpx.AsyncClient, saved_queries: InMemorySavedQueries
    ) -> None:
        created = await save(saved_client)
        saved_queries.corrupt.add(UUID(created["id"]))

        run = await saved_client.get(f"/queries/{created['id']}/elements")

        assert (run.status_code, run.json()["error"]) == (422, "invalid_query")
        assert (await saved_client.get("/queries")).status_code == 200

    async def test_a_page_outside_the_bounds_is_a_422(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        created = await save(saved_client)

        response = await saved_client.get(f"/queries/{created['id']}/elements", params={"limit": 0})

        assert response.status_code == 422


class TestWhoMayDoWhat:
    async def test_a_reader_lists_reads_and_runs_but_cannot_write(
        self,
        service: ArchitectureService,
        packages: InMemoryPackages,
        saved_queries: InMemorySavedQueries,
        nobody_calling: None,
    ) -> None:
        verifier = StaticVerifier({"ed": an_editor(), "rd": a_reader()})
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            packages=packages,  # type: ignore[arg-type]
            queries=saved_queries,  # type: ignore[arg-type]
            verifier=verifier,
        )
        editor, reader = {"Authorization": "Bearer ed"}, {"Authorization": "Bearer rd"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = (
                await client.post("/queries", json={"name": "A", "query": body()}, headers=editor)
            ).json()

            for path in (
                "/queries",
                f"/queries/{created['id']}",
                f"/queries/{created['id']}/elements",
            ):
                assert (await client.get(path, headers=reader)).status_code == 200
            for request in (
                client.post("/queries", json={"name": "B", "query": body()}, headers=reader),
                client.patch(f"/queries/{created['id']}", json={"name": "C"}, headers=reader),
                client.delete(f"/queries/{created['id']}", headers=reader),
            ):
                response = await request
                assert (response.status_code, response.json()["error"]) == (403, "forbidden")


class TestNoNulReachesTheStore:
    async def test_a_nul_in_the_name_is_a_422_not_a_500(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        response = await saved_client.post("/queries", json={"name": "a\u0000b", "query": body()})

        assert (response.status_code, response.json()["error"]) == (422, "invalid_input")

    async def test_a_nul_in_the_filter_is_a_422_invalid_query(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        response = await saved_client.post(
            "/queries", json={"name": "A", "query": body(text="a\u0000b")}
        )

        assert (response.status_code, response.json()["error"]) == (422, "invalid_query")
