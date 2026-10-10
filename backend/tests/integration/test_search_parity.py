"""`GET /elements` and `POST /elements/search` are one filter: same SQL, same answer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.core.config import Settings
from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.model import Element
from ea.domain.packages import Package
from ea.domain.query import ElementQuery, to_json
from ea.main import create_app
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.package_store import PostgresPackageRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def client(engine_at_head: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    packages = PostgresPackageRepository(sessions)
    finance = await packages.add(Package.create(name="Finance", now=NOW))
    for name, kind, filed in (
        ("billing", E.APPLICATION_COMPONENT, True),
        ("ledger", E.APPLICATION_COMPONENT, False),
        ("db_01", E.NODE, True),
        ("dbx01", E.NODE, False),
    ):
        element = await graph.add_element(Element.create(element_type=kind, name=name, now=NOW))
        if filed:
            await packages.move_elements([element.id], finance.id)
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=ArchitectureService(graph, clock=lambda: NOW),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.headers["x-finance"] = str(finance.id)
        yield client


async def both(client: httpx.AsyncClient, params: dict[str, Any], **query: Any) -> None:
    got = (await client.get("/elements", params=params)).json()
    posted = (await client.post("/elements/search", json=to_json(ElementQuery(**query)))).json()
    assert got == posted
    assert got["total"] >= 0


async def test_the_same_filter_gives_the_same_page_by_both_doors(
    client: httpx.AsyncClient,
) -> None:
    finance = client.headers["x-finance"]
    await both(client, {})
    await both(client, {"element_type": "node"}, element_types=(E.NODE,))
    await both(client, {"search": "db_"}, text="db_")
    await both(client, {"search": "%"}, text="%")
    await both(client, {"package": finance}, package_id=UUID(finance))
    await both(client, {"root_only": "true"}, root_only=True)
    await both(client, {"limit": 1, "offset": 1}, limit=1, offset=1)
    found = (await client.get("/elements", params={"search": "db_"})).json()
    assert [item["name"] for item in found["items"]] == ["db_01"]


async def test_a_nul_byte_is_a_typed_422_not_a_500(client: httpx.AsyncClient) -> None:
    def post(**changes: Any) -> Any:
        return client.post("/elements/search", json={**to_json(ElementQuery()), **changes})

    nul_property = [{"key": "owner", "op": "eq", "value": "a\u0000b"}]
    for response in (
        await post(text="a\u0000b"),
        await post(properties=nul_property),
        await client.get("/elements", params={"search": "a\u0000b"}),
    ):
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_query"


async def test_the_text_is_trimmed_by_both_doors(client: httpx.AsyncClient) -> None:
    await both(client, {"search": " db_ "}, text=" db_ ")
    await both(client, {"search": "   "}, text="   ")
    blank = (await client.get("/elements", params={"search": "   "})).json()
    assert blank["total"] == 4
    padded = (await client.get("/elements", params={"search": " db_"})).json()
    assert [item["name"] for item in padded["items"]] == ["db_01"]
