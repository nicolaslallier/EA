"""Over HTTP too, the trail names the verified caller and the HTTP door (docs/adr/0044)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.audit import AuditChannel
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryAuditLog, StaticVerifier, a_reader, an_editor

ALICE, BOB = "alice-token", "bob-token"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, nobody_calling: None
) -> AsyncIterator[httpx.AsyncClient]:
    verifier = StaticVerifier(
        {
            ALICE: an_editor(subject="sub-alice", username="alice"),
            BOB: an_editor(subject="sub-bob", username="bob"),
            "reader": a_reader(),
        }
    )
    app = create_app(
        Settings(debug=True, auth_enabled=True), architecture_service=service, verifier=verifier
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def new_node(name: str, **extra: object) -> dict[str, object]:
    return {"element_type": "node", "name": name, **extra}


@pytest.mark.asyncio
class TestTheIdentityOfAnHttpEvent:
    async def test_the_actor_is_the_token_and_the_door_is_http(
        self, client: httpx.AsyncClient, audit_log: InMemoryAuditLog
    ) -> None:
        response = await client.post("/elements", json=new_node("a"), headers=bearer(ALICE))

        assert response.status_code == 201
        (event,) = audit_log.events
        assert (event.actor_sub, event.actor_name) == ("sub-alice", "alice")
        assert event.channel is AuditChannel.HTTP

    async def test_a_body_cannot_name_an_actor_or_a_door(
        self, client: httpx.AsyncClient, audit_log: InMemoryAuditLog
    ) -> None:
        response = await client.post(
            "/elements",
            json=new_node("a", actor_sub="evil", channel="mcp", request_id="x"),
            headers=bearer(ALICE),
        )

        assert response.status_code == 422
        assert audit_log.events == []

    async def test_a_reader_writes_nothing_and_leaves_no_event(
        self, client: httpx.AsyncClient, audit_log: InMemoryAuditLog
    ) -> None:
        response = await client.post("/elements", json=new_node("a"), headers=bearer("reader"))

        assert response.status_code == 403
        assert audit_log.events == []

    async def test_a_forged_request_id_header_is_replaced_not_recorded(
        self, client: httpx.AsyncClient, audit_log: InMemoryAuditLog
    ) -> None:
        await client.post(
            "/elements",
            json=new_node("a"),
            headers={**bearer(ALICE), "X-Request-Id": "trace-1"},
        )
        await client.post(
            "/elements",
            json=new_node("b"),
            headers={**bearer(ALICE), "X-Request-Id": "x" * 200},
        )

        kept, replaced = audit_log.events
        assert kept.request_id == "trace-1"
        assert re.fullmatch(r"[0-9a-f]{12}", replaced.request_id)

    async def test_two_requests_in_flight_never_swap_actors(
        self, client: httpx.AsyncClient, audit_log: InMemoryAuditLog
    ) -> None:
        async def create(token: str, name: str) -> None:
            await client.post("/elements", json=new_node(name), headers=bearer(token))

        await asyncio.gather(
            *(create(ALICE if i % 2 else BOB, f"n{i}") for i in range(12)),
        )

        assert len(audit_log.events) == 12
        for event in audit_log.events:
            assert event.after is not None
            index = int(event.after["name"].removeprefix("n"))
            assert event.actor_name == ("alice" if index % 2 else "bob")


@pytest.mark.asyncio
async def test_with_auth_off_the_event_names_the_local_developer_explicitly(
    service: ArchitectureService, audit_log: InMemoryAuditLog
) -> None:
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/elements", json=new_node("a"))

    assert response.status_code == 201
    (event,) = audit_log.events
    assert event.actor_sub == "local-developer"
    assert event.channel is AuditChannel.HTTP
