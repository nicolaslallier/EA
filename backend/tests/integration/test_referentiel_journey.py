"""The journey of the whole sub-project, over HTTP, on a real PostgreSQL.

Create a package, file an element in it, search for "sous-arbre", keep the
query, run it again — then delete the package and see the query explain
itself. The doubled suites cannot prove it: their graph does not filter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.core.config import Settings
from ea.db.postgres import create_session_factory
from ea.domain.query import ElementQuery, to_json
from ea.main import create_app
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.package_store import PostgresPackageRepository
from ea.repositories.saved_query_store import PostgresSavedQueryRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def client(engine_at_head: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    sessions = create_session_factory(engine_at_head)
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=ArchitectureService(PostgresArchitectureRepository(sessions)),
        diagrams=PostgresDiagramRepository(sessions),
        packages=PostgresPackageRepository(sessions),
        queries=PostgresSavedQueryRepository(sessions),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def body(**changes: Any) -> dict[str, Any]:
    return {**to_json(ElementQuery()), **changes}


async def made(client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(path, json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_create_file_search_save_run_and_survive_a_deleted_package(
    client: httpx.AsyncClient,
) -> None:
    finance = await made(client, "/packages", {"name": "Finance"})
    budget = await made(client, "/packages", {"name": "Budget", "parent_id": finance["id"]})
    inside = await made(
        client,
        "/elements",
        {"element_type": "application_component", "name": "Facturation sous-arbre",
         "properties": {"owner": "finance"}},
    )  # fmt: skip
    outside = await made(
        client, "/elements", {"element_type": "application_component", "name": "Facturation racine"}
    )
    assert outside["package_id"] is None
    filed = await client.post(
        "/elements/move", json={"element_ids": [inside["id"]], "package_id": budget["id"]}
    )
    assert filed.status_code == 204

    # The catalogue's own listing and the structured search agree on the subtree.
    listed = (await client.get("/elements", params={"package": finance["id"]})).json()
    assert [e["name"] for e in listed["items"]] == ["Facturation sous-arbre"]

    query = body(
        text="sous-arbre",
        package_id=finance["id"],
        properties=[{"key": "owner", "op": "eq", "value": "finance"}],
    )
    found = (await client.post("/elements/search", json=query)).json()
    assert (found["total"], [e["name"] for e in found["items"]]) == (1, ["Facturation sous-arbre"])

    without_children = (
        await client.post("/elements/search", json={**query, "include_subpackages": False})
    ).json()
    assert without_children["total"] == 0

    saved = await made(client, "/queries", {"name": "Compta", "description": "d", "query": query})
    assert saved["created_by"] == "local-developer"
    replay = (await client.get(f"/queries/{saved['id']}/elements")).json()
    assert [e["name"] for e in replay["items"]] == ["Facturation sous-arbre"]

    # A package that still holds something is not deleted; moving its content is.
    refused = await client.delete(f"/packages/{finance['id']}")
    assert (refused.status_code, refused.json()["error"]) == (409, "package_not_empty")
    gone = await client.delete(f"/packages/{budget['id']}", params={"move_contents_to": "root"})
    assert gone.status_code == 204
    assert (await client.get(f"/elements/{inside['id']}")).json()["package_id"] is None

    # The query names the package by id and keeps no foreign key: it survives and explains.
    await client.delete(f"/packages/{finance['id']}")
    stale = await client.get(f"/queries/{saved['id']}/elements")
    assert (stale.status_code, stale.json()["error"]) == (422, "unknown_package")
    assert (await client.get(f"/queries/{saved['id']}")).status_code == 200
    assert [q["name"] for q in (await client.get("/queries")).json()] == ["Compta"]
