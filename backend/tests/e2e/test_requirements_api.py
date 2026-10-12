"""The coverage endpoint, end to end against a doubled graph."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, repository: InMemoryRepository
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        requirements=repository,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def make(
    client: httpx.AsyncClient, element_type: str, name: str, **properties: str
) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": element_type, "name": name, "properties": properties}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def realize(
    client: httpx.AsyncClient, source: dict[str, Any], target: dict[str, Any]
) -> None:
    response = await client.post(
        "/relationships",
        json={
            "relationship_type": "realization",
            "source_id": source["id"],
            "target_id": target["id"],
        },
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
class TestCoverageEndpoint:
    async def test_an_empty_catalogue_has_no_rate(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/requirements/coverage")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["design_rate"] is None
        assert body["verification_rate"] is None
        assert body["requirements"] == []

    async def test_the_gaps_are_reported(self, client: httpx.AsyncClient) -> None:
        orphan = await make(client, "requirement", "Sans conception", req_id="REQ-1")
        untested = await make(client, "requirement", "Sans test", req_status="approved")
        done = await make(client, "requirement", "Complète", req_priority="must")
        design = await make(client, "application_component", "Coffre")
        test = await make(client, "deliverable", "Test chiffrement", test_result="passed")
        await realize(client, design, untested)
        await realize(client, design, done)
        await realize(client, test, done)

        body = (await client.get("/requirements/coverage")).json()

        gap_of = {item["name"]: item["gap"] for item in body["requirements"]}
        assert gap_of == {
            "Sans conception": "design",
            "Sans test": "verification",
            "Complète": "none",
        }
        assert body["total"] == 3
        assert body["designed"] == 2
        assert body["verified"] == 1
        assert body["design_rate"] == pytest.approx(2 / 3)
        done_item = next(item for item in body["requirements"] if item["name"] == "Complète")
        assert [e["name"] for e in done_item["designed_by"]] == ["Coffre"]
        assert done_item["tested_by"][0]["result"] == "passed"
        orphan_item = next(i for i in body["requirements"] if i["id"] == orphan["id"])
        assert orphan_item["identifier"] == "REQ-1"
        assert orphan_item["status"] == "unset"

    async def test_the_list_can_be_narrowed_by_status(self, client: httpx.AsyncClient) -> None:
        await make(client, "requirement", "A", req_status="approved")
        await make(client, "requirement", "B", req_status="proposed")

        body = (await client.get("/requirements/coverage", params={"status": "approved"})).json()

        assert [item["name"] for item in body["requirements"]] == ["A"]
        assert body["by_status"]["approved"] == 1
        assert body["by_status"]["proposed"] == 0

    async def test_an_unknown_status_is_a_422_with_the_typed_envelope(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/requirements/coverage", params={"status": "banana"})

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_input"
        assert "banana" not in response.json()["detail"]  # the value is never echoed


@pytest.mark.asyncio
async def test_a_failing_test_does_not_verify(client: httpx.AsyncClient) -> None:
    requirement = await make(client, "requirement", "R")
    design = await make(client, "application_component", "D")
    red = await make(client, "deliverable", "T", test_result="failed")
    await realize(client, design, requirement)
    await realize(client, red, requirement)

    body = (await client.get("/requirements/coverage")).json()

    assert (body["verified"], body["failing"], body["unverified"]) == (0, 1, 0)
    assert body["verification_rate"] == 0.0
    assert body["requirements"][0]["verification"] == "failing"
    assert body["requirements"][0]["gap"] == "verification"


@pytest.mark.asyncio
async def test_the_write_and_the_coverage_agree_on_what_a_result_is(
    client: httpx.AsyncClient,
) -> None:
    bad = await client.post(
        "/elements",
        json={"element_type": "deliverable", "name": "T", "properties": {"test_result": "ok"}},
    )
    good = await client.post(
        "/elements",
        json={"element_type": "deliverable", "name": "T", "properties": {"test_result": " Passed"}},
    )

    assert bad.status_code == 422 and bad.json()["error"] == "invalid_requirement"
    assert "ok" not in bad.json()["detail"].replace("one of", "")
    assert good.status_code == 201 and good.json()["properties"] == {"test_result": "passed"}
