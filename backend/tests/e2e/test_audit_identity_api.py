"""Over HTTP too, the trail names the verified caller and the HTTP door (docs/adr/0046)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.core.logging import REQUESTS_LOGGER
from ea.domain.audit import AuditChannel
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    InMemoryAuditLog,
    StaticVerifier,
    a_reader,
    an_editor,
    an_empty_package_tree,
)

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
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        verifier=verifier,
        packages=an_empty_package_tree(),
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

    async def test_the_request_id_of_an_event_is_always_the_servers(
        self, client: httpx.AsyncClient, audit_log: InMemoryAuditLog
    ) -> None:
        first = await client.post(
            "/elements", json=new_node("a"), headers={**bearer(ALICE), "X-Request-Id": "trace-1"}
        )
        second = await client.post(
            "/elements", json=new_node("b"), headers={**bearer(ALICE), "X-Request-Id": "x" * 200}
        )

        one, two = audit_log.events
        for event in (one, two):
            assert re.fullmatch(r"[0-9a-f]{12}", event.request_id)
        assert one.request_id != "trace-1"
        # What the SPA prints from the response is what the trail recorded.
        assert first.headers["x-request-id"] == one.request_id
        assert second.headers["x-request-id"] == two.request_id

    async def test_a_caller_cannot_choose_the_request_id_of_another(
        self, client: httpx.AsyncClient, audit_log: InMemoryAuditLog
    ) -> None:
        """Two callers sending the same id still get two distinct, server-made ones."""
        headers = {"X-Request-Id": "shared-id"}
        await client.post("/elements", json=new_node("a"), headers={**bearer(ALICE), **headers})
        await client.post("/elements", json=new_node("b"), headers={**bearer(BOB), **headers})

        alice, bob = audit_log.events
        assert alice.request_id != bob.request_id
        assert "shared-id" not in (alice.request_id, bob.request_id)

    async def test_the_clients_own_id_is_logged_apart(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=REQUESTS_LOGGER):
            await client.get("/me", headers={**bearer(ALICE), "X-Request-Id": "trace-9"})

        record = next(r for r in caplog.records if r.name == REQUESTS_LOGGER)
        assert record.client_request_id == "trace-9"  # type: ignore[attr-defined]

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
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/elements", json=new_node("a"))

    assert response.status_code == 201
    (event,) = audit_log.events
    assert event.actor_sub == "local-developer"
    assert event.channel is AuditChannel.HTTP
