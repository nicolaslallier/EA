"""`POST /elements/search` — the contract, against a doubled graph.

The double does not filter (tests/conftest.py), so what is proved here is the
door: the body, the page, the refusals. What the SQL does with a query is
`tests/integration/test_element_search.py`'s.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.archimate import ElementType
from ea.domain.query import ElementQuery, to_json
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import StaticVerifier, a_reader, an_editor

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(service: ArchitectureService) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
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
        Settings(debug=True, auth_enabled=True), architecture_service=service, verifier=verifier
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
