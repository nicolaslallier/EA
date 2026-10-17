"""The publication endpoints, against doubled stores — every status of the contract."""

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
    InMemoryPackages,
    StaticVerifier,
    a_reader,
    an_approver,
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


async def a_package(client: httpx.AsyncClient, name: str = "Finance") -> dict[str, Any]:
    response = await client.post("/packages", json={"name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_publishing_previews_by_default_then_publishes(client: httpx.AsyncClient) -> None:
    package = await a_package(client)
    url = f"/packages/{package['id']}/publish"

    preview = await client.post(url, json={"bump": "minor", "notes": "Première"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["published"] is None
    assert preview.json()["preview"]["version"] == "v1.0.0"

    done = await client.post(url, json={"bump": "minor", "notes": "Première", "dry_run": False})
    assert done.status_code == 200, done.text
    assert done.json()["published"]["version"] == "v1.0.0"

    read = (await client.get(f"/packages/{package['id']}")).json()
    assert read["frozen_version"] == "v1.0.0"
    assert read["frozen_by"]["version"] == "v1.0.0"
    assert read["allowed_actions"] == ["reopen"]

    again = await client.post(url, json={"bump": "minor", "notes": "x", "dry_run": False})
    assert (again.status_code, again.json()["error"]) == (409, "package_already_frozen")


async def test_the_versions_are_listed_and_reopening_closes_one(client: httpx.AsyncClient) -> None:
    package = await a_package(client)
    await client.post(
        f"/packages/{package['id']}/publish",
        json={"bump": "minor", "notes": "n", "dry_run": False},
    )
    reopened = await client.post(f"/packages/{package['id']}/reopen", json={"reason": "coquille"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["reopen_reason"] == "coquille"
    listed = (await client.get(f"/packages/{package['id']}/versions")).json()
    assert [v["version"] for v in listed] == ["v1.0.0"]
    assert listed[0]["reopened_at"] is not None
    nothing = await client.post(f"/packages/{package['id']}/reopen", json={"reason": "encore"})
    assert (nothing.status_code, nothing.json()["error"]) == (409, "package_not_frozen")


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ({"bump": "major", "notes": "n"}, 422, "adr_required"),
        ({"bump": "minor", "notes": "   "}, 422, "invalid_publication"),
        ({"bump": "minor", "notes": "n" * 4001}, 422, None),
        ({"bump": "huge", "notes": "n"}, 422, None),
        ({"bump": "minor", "notes": "n", "surplus": 1}, 422, None),
    ],
)
async def test_what_cannot_be_published_is_refused_typed(
    client: httpx.AsyncClient, body: dict[str, Any], status: int, error: str | None
) -> None:
    package = await a_package(client)
    response = await client.post(f"/packages/{package['id']}/publish", json=body)
    assert response.status_code == status, response.text
    if error is not None:
        assert response.json()["error"] == error


async def test_an_unknown_package_is_a_404(client: httpx.AsyncClient) -> None:
    missing = "00000000-0000-4000-8000-000000000000"
    for method, path, body in (
        ("post", f"/packages/{missing}/publish", {"bump": "minor", "notes": "n"}),
        ("post", f"/packages/{missing}/reopen", {"reason": "r"}),
        ("get", f"/packages/{missing}/versions", None),
    ):
        response = await client.request(method, path, json=body)
        assert response.status_code == 404, (path, response.text)


async def test_who_may_publish(service: ArchitectureService, packages: InMemoryPackages) -> None:
    verifier = StaticVerifier({"ap": an_approver(), "ed": an_editor(), "rd": a_reader()})
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
            "/packages", json={"name": "Finance"}, headers={"Authorization": "Bearer ed"}
        )
        package_id = created.json()["id"]
        body = {"bump": "minor", "notes": "n", "dry_run": False}
        anonymous = await client.post(f"/packages/{package_id}/publish", json=body)
        assert anonymous.status_code == 401
        for token in ("ed", "rd"):
            refused = await client.post(
                f"/packages/{package_id}/publish",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert (refused.status_code, refused.json()["error"]) == (403, "forbidden")
        allowed = await client.post(
            f"/packages/{package_id}/publish", json=body, headers={"Authorization": "Bearer ap"}
        )
        assert allowed.status_code == 200, allowed.text


@pytest_asyncio.fixture
async def published_finance(client: httpx.AsyncClient) -> dict[str, str]:
    """Finance (v1.0.0 in force) > Budget, holding one element and one diagram."""
    finance = await a_package(client)
    budget = (
        await client.post("/packages", json={"name": "Budget", "parent_id": finance["id"]})
    ).json()
    element = await client.post(
        "/elements", json={"element_type": "application_component", "name": "Billing"}
    )
    assert element.status_code == 201, element.text
    moved = await client.post(
        "/elements/move", json={"element_ids": [element.json()["id"]], "package_id": budget["id"]}
    )
    assert moved.status_code == 204, moved.text
    diagram = await client.post("/diagrams", json={"name": "Vue", "package_id": budget["id"]})
    assert diagram.status_code == 201, diagram.text
    published = await client.post(
        f"/packages/{finance['id']}/publish",
        json={"bump": "minor", "notes": "n", "dry_run": False},
    )
    assert published.status_code == 200, published.text
    return {
        "finance": finance["id"],
        "budget": budget["id"],
        "inside": element.json()["id"],
        "diagram": diagram.json()["id"],
    }


async def test_an_element_below_a_published_package_says_who_froze_it(
    client: httpx.AsyncClient, published_finance: dict[str, str]
) -> None:
    page = (await client.get("/elements", params={"package": published_finance["budget"]})).json()
    assert page["items"]
    assert {item["frozen_by"]["version"] for item in page["items"]} == {"v1.0.0"}
    one = (await client.get(f"/elements/{published_finance['inside']}")).json()
    assert one["frozen_by"]["path"] == "Finance" and one["allowed_transitions"] == []


async def test_a_diagram_below_a_published_package_says_so(
    client: httpx.AsyncClient, published_finance: dict[str, str]
) -> None:
    read = (await client.get(f"/diagrams/{published_finance['diagram']}")).json()
    assert read["frozen_by"]["version"] == "v1.0.0"


async def test_reopening_clears_frozen_by_on_the_next_read(
    client: httpx.AsyncClient, published_finance: dict[str, str]
) -> None:
    reopened = await client.post(
        f"/packages/{published_finance['finance']}/reopen", json={"reason": "correction"}
    )
    assert reopened.status_code == 200, reopened.text
    read = (await client.get(f"/elements/{published_finance['inside']}")).json()
    assert read["frozen_by"] is None
