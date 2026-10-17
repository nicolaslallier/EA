"""Review over HTTP: 403 for the wrong role, 409 for the wrong state or revision, and what the
SPA is told (docs/adr/0048)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import StaticVerifier, a_reader, an_approver, an_editor, an_empty_package_tree

pytestmark = pytest.mark.asyncio

EDITOR, APPROVER, READER = "editor-token", "approver-token", "reader-token"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, nobody_calling: None
) -> AsyncIterator[httpx.AsyncClient]:
    verifier = StaticVerifier({EDITOR: an_editor(), APPROVER: an_approver(), READER: a_reader()})
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        verifier=verifier,
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def as_(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": "node", "name": "db-01"}, headers=as_(EDITOR)
    )
    return dict(response.json())


async def move(
    client: httpx.AsyncClient,
    element: dict[str, Any],
    transition: str,
    token: str,
    **extra: object,
) -> httpx.Response:
    return await client.post(
        f"/elements/{element['id']}/approval",
        json={"transition": transition, **extra},
        headers=as_(token),
    )


async def test_an_element_is_born_a_draft_and_tells_each_caller_what_they_may_do(
    client: httpx.AsyncClient,
) -> None:
    element = await make(client)

    as_editor = (await client.get(f"/elements/{element['id']}", headers=as_(EDITOR))).json()
    as_reader = (await client.get(f"/elements/{element['id']}", headers=as_(READER))).json()

    assert (as_editor["approval_state"], as_editor["locked"]) == ("draft", False)
    assert as_editor["approved_by"] is None and as_editor["revision"] == 1
    assert as_editor["allowed_transitions"] == ["submit"]
    assert as_reader["allowed_transitions"] == []


async def test_the_review_flow_over_http(client: httpx.AsyncClient) -> None:
    element = await make(client)

    submitted = await move(client, element, "submit", EDITOR)
    approved = await move(client, element, "approve", APPROVER, expected_revision=1)
    locked = await move(client, element, "lock", APPROVER)

    assert submitted.json()["approval_state"] == "in_review"
    assert submitted.json()["allowed_transitions"] == []  # an editor cannot approve
    assert approved.json()["approved_by"] == "approver-1"
    # `/approval` answers for the caller of that call: the approver, who may reopen or lock.
    assert approved.json()["allowed_transitions"] == ["reopen", "lock"]
    assert locked.json()["locked"] is True


async def test_403_for_the_wrong_role_409_for_the_wrong_state_401_without_a_token(
    client: httpx.AsyncClient,
) -> None:
    element = await make(client)
    await move(client, element, "submit", EDITOR)

    assert (await move(client, element, "approve", EDITOR, expected_revision=1)).status_code == 403
    assert (await move(client, element, "submit", READER)).status_code == 403
    wrong_state = await move(client, element, "lock", APPROVER)
    assert wrong_state.status_code == 409
    assert wrong_state.json()["error"] == "invalid_transition"
    anonymous = await client.post(
        f"/elements/{element['id']}/approval", json={"transition": "submit"}
    )
    assert anonymous.status_code == 401
    assert (await move(client, element, "teleport", APPROVER)).status_code == 422


async def test_approving_needs_the_revision_read_and_a_stale_one_is_a_409(
    client: httpx.AsyncClient,
) -> None:
    element = await make(client)
    await move(client, element, "submit", EDITOR)
    await client.patch(f"/elements/{element['id']}", json={"description": "x"}, headers=as_(EDITOR))

    without = await move(client, element, "approve", APPROVER)
    stale = await move(client, element, "approve", APPROVER, expected_revision=1)
    fresh = (await client.get(f"/elements/{element['id']}", headers=as_(APPROVER))).json()
    approved = await move(client, element, "approve", APPROVER, expected_revision=fresh["revision"])

    assert without.status_code == 422
    assert stale.status_code == 409 and stale.json()["error"] == "approval_stale"
    assert approved.status_code == 200
    assert (await move(client, element, "reopen", APPROVER, expected_revision=0)).status_code == 422


async def test_an_approved_element_refuses_an_editor_with_a_409(client: httpx.AsyncClient) -> None:
    element = await make(client)
    await move(client, element, "submit", EDITOR)
    await move(client, element, "approve", APPROVER, expected_revision=1)

    refused = await client.patch(
        f"/elements/{element['id']}", json={"name": "db-02"}, headers=as_(EDITOR)
    )
    deleted = await client.delete(f"/elements/{element['id']}", headers=as_(EDITOR))
    allowed = await client.patch(
        f"/elements/{element['id']}", json={"name": "db-02"}, headers=as_(APPROVER)
    )

    assert refused.status_code == 409 and refused.json()["error"] == "element_locked"
    assert deleted.status_code == 409
    assert allowed.status_code == 200
    assert allowed.json()["approval_state"] == "in_review"


async def test_me_says_whether_the_caller_may_approve(client: httpx.AsyncClient) -> None:
    approver = (await client.get("/me", headers=as_(APPROVER))).json()
    editor = (await client.get("/me", headers=as_(EDITOR))).json()

    assert (approver["can_write"], approver["can_approve"]) == (True, True)
    assert (editor["can_write"], editor["can_approve"]) == (True, False)
