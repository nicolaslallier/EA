"""The external-reference endpoints, end to end against doubled stores and providers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.errors import ExternalUnavailableError
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    FakeExternalSystem,
    InMemoryExternalReferences,
    StaticVerifier,
    a_reader,
    an_editor,
)

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"


@pytest_asyncio.fixture
async def system() -> FakeExternalSystem:
    return FakeExternalSystem()


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService,
    external_references: InMemoryExternalReferences,
    system: FakeExternalSystem,
    nobody_calling: None,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        external=external_references,
        external_systems={"main": system},
        external_inactive={"offline": "jira_issue"},  # type: ignore[arg-type]
        verifier=StaticVerifier({EDITOR: an_editor(), READER: a_reader()}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def auth(token: str = EDITOR) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def an_element(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/elements",
        json={"element_type": "application_component", "name": "Billing"},
        headers=auth(),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def link(
    client: httpx.AsyncClient, element: str, reference: str = "acme/billing#12", **extra: Any
) -> httpx.Response:
    return await client.post(
        f"/elements/{element}/external-references",
        json={"kind": "github_issue", "reference": reference, **extra},
        headers=auth(),
    )


async def test_linking_returns_the_cached_title_and_never_an_address_of_the_provider(
    client: httpx.AsyncClient,
) -> None:
    element = await an_element(client)

    response = await link(client, element, target="main")

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {
        "id", "element_id", "kind", "target", "external_id", "url", "title",
        "status", "synced_at", "created_by", "created_at", "stale",
    }  # fmt: skip
    assert (body["title"], body["status"], body["stale"]) == ("Fix invoice rounding", "open", False)
    assert body["url"] == "https://github.test/acme/billing/issues/12"


async def test_listing_serves_the_cache_without_calling_the_provider(
    client: httpx.AsyncClient, system: FakeExternalSystem
) -> None:
    element = await an_element(client)
    await link(client, element)
    system.calls.clear()

    response = await client.get(f"/elements/{element}/external-references", headers=auth(READER))

    assert response.status_code == 200
    assert [r["external_id"] for r in response.json()] == ["acme/billing#12"]
    assert system.calls == []


async def test_the_targets_are_listed_by_name_and_kind_only(client: httpx.AsyncClient) -> None:
    response = await client.get("/external/targets", headers=auth(READER))

    assert response.status_code == 200
    assert response.json() == [
        {"name": "main", "kind": "github_issue", "active": True, "writable": True},
        {"name": "offline", "kind": "jira_issue", "active": False, "writable": False},
    ]


async def test_the_refusals_are_typed(
    client: httpx.AsyncClient, system: FakeExternalSystem
) -> None:
    element = await an_element(client)
    assert (await link(client, element)).status_code == 201

    duplicate = await link(client, element, "Acme/Billing#12")
    foreign = await link(client, element, "https://evil.test/acme/billing/issues/12")
    unknown = await link(client, element, "acme/billing#999")
    nowhere = await link(client, element, target="nope")
    missing = await link(client, str(uuid4()))
    system.failure = ExternalUnavailableError("down")
    down = await link(client, element, "acme/billing#13")

    assert (duplicate.status_code, duplicate.json()["error"]) == (409, "duplicate")
    assert (foreign.status_code, foreign.json()["error"]) == (422, "invalid_external_reference")
    assert (unknown.status_code, unknown.json()["error"]) == (422, "external_refused")
    assert (nowhere.status_code, nowhere.json()["error"]) == (422, "unknown_external_target")
    assert missing.status_code == 404
    assert (down.status_code, down.json()["error"]) == (503, "external_unavailable")


async def test_a_body_with_an_address_field_is_refused_a_caller_never_gives_one(
    client: httpx.AsyncClient,
) -> None:
    element = await an_element(client)

    response = await link(client, element, api_url="https://evil.test", host="evil.test")

    assert response.status_code == 422


async def test_the_reference_is_bounded_before_it_reaches_the_service(
    client: httpx.AsyncClient,
) -> None:
    element = await an_element(client)

    response = await link(client, element, "x" * 5000)

    assert response.status_code == 422


async def test_a_reader_reads_and_cannot_write(client: httpx.AsyncClient) -> None:
    element = await an_element(client)
    created = (await link(client, element)).json()

    write = await client.post(
        f"/elements/{element}/external-references",
        json={"kind": "url", "reference": "https://wiki.test/a"},
        headers=auth(READER),
    )
    delete = await client.delete(f"/external-references/{created['id']}", headers=auth(READER))
    refresh = await client.post(
        f"/external-references/{created['id']}/refresh", headers=auth(READER)
    )
    issue = await client.post(
        f"/elements/{element}/external-issues",
        json={"target": "main", "scope": "acme/billing", "title": "t"},
        headers=auth(READER),
    )

    assert [r.status_code for r in (write, delete, refresh, issue)] == [403, 403, 403, 403]


async def test_every_route_wants_a_token(client: httpx.AsyncClient) -> None:
    element = await an_element(client)
    some = uuid4()

    answers = [
        await client.get(f"/elements/{element}/external-references"),
        await client.post(f"/elements/{element}/external-references", json={}),
        await client.post(f"/elements/{element}/external-references/refresh"),
        await client.post(f"/elements/{element}/external-issues", json={}),
        await client.post(f"/external-references/{some}/refresh"),
        await client.delete(f"/external-references/{some}"),
        await client.get("/external-references/stale"),
        await client.get("/external/targets"),
    ]

    assert {a.status_code for a in answers} == {401}


async def test_refresh_returns_the_line_and_a_provider_down_means_stale_not_an_error(
    client: httpx.AsyncClient, system: FakeExternalSystem
) -> None:
    element = await an_element(client)
    created = (await link(client, element)).json()
    system.failure = ExternalUnavailableError("down")

    one = await client.post(f"/external-references/{created['id']}/refresh", headers=auth())
    every = await client.post(f"/elements/{element}/external-references/refresh", headers=auth())

    assert one.status_code == 200 and one.json()["stale"] is True
    assert every.status_code == 200 and every.json()[0]["stale"] is True
    missing = await client.post(f"/external-references/{uuid4()}/refresh", headers=auth())
    assert missing.status_code == 404


async def test_unlinking_removes_the_link_only(client: httpx.AsyncClient) -> None:
    element = await an_element(client)
    created = (await link(client, element)).json()

    gone = await client.delete(f"/external-references/{created['id']}", headers=auth())
    again = await client.delete(f"/external-references/{created['id']}", headers=auth())

    assert (gone.status_code, again.status_code) == (204, 404)


async def test_the_stale_listing_is_bounded(client: httpx.AsyncClient) -> None:
    ok = await client.get("/external-references/stale?limit=5", headers=auth(READER))
    too_big = await client.get("/external-references/stale?limit=100000", headers=auth(READER))
    zero = await client.get("/external-references/stale?limit=0", headers=auth(READER))

    assert ok.status_code == 200
    assert too_big.status_code == 422
    assert zero.status_code == 422


async def test_opening_an_issue_is_a_dry_run_unless_told_otherwise(
    client: httpx.AsyncClient, system: FakeExternalSystem
) -> None:
    element = await an_element(client)
    payload = {"target": "main", "scope": "acme/billing", "title": "Rounding", "body": "why"}

    plan = await client.post(f"/elements/{element}/external-issues", json=payload, headers=auth())
    done = await client.post(
        f"/elements/{element}/external-issues", json={**payload, "dry_run": False}, headers=auth()
    )

    assert plan.status_code == 200
    assert plan.json()["dry_run"] is True and plan.json()["reference"] is None
    assert done.status_code == 201
    assert done.json()["dry_run"] is False
    assert done.json()["reference"]["external_id"] == "acme/billing#101"
    assert len(system.created) == 1


async def test_a_read_only_target_refuses_an_issue_even_with_dry_run_false(
    service: ArchitectureService,
    external_references: InMemoryExternalReferences,
    nobody_calling: None,
) -> None:
    readonly = FakeExternalSystem("main", writable=False)
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        external=external_references,
        external_systems={"main": readonly},
        verifier=StaticVerifier({EDITOR: an_editor()}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        element = await an_element(client)
        response = await client.post(
            f"/elements/{element}/external-issues",
            json={"target": "main", "scope": "acme/billing", "title": "t", "dry_run": False},
            headers=auth(),
        )

    assert (response.status_code, response.json()["error"]) == (422, "unknown_external_target")
    assert readonly.create_attempts == 0


@pytest.mark.parametrize("value", ["no", "false", "off", "n", 0, "0", None, "", [], "False"])
async def test_dry_run_is_a_strict_boolean_over_http_so_no_odd_value_opens_a_ticket(
    client: httpx.AsyncClient, system: FakeExternalSystem, value: object
) -> None:
    element = await an_element(client)

    response = await client.post(
        f"/elements/{element}/external-issues",
        json={"target": "main", "scope": "acme/billing", "title": "t", "dry_run": value},
        headers=auth(),
    )

    assert response.status_code == 422
    assert system.created == []
