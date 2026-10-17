"""The audit endpoints, end to end against doubled stores."""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    FIXED_NOW,
    InMemoryAuditLog,
    InMemoryRepository,
    StaticVerifier,
    a_reader,
    an_editor,
    an_empty_package_tree,
)

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"


@pytest.fixture
def service(repository: InMemoryRepository) -> ArchitectureService:
    """A clock that ticks: with every event at one instant the order would be the ids' luck."""
    ticks = itertools.count()
    return ArchitectureService(repository, clock=lambda: FIXED_NOW + timedelta(seconds=next(ticks)))


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, audit_log: InMemoryAuditLog, nobody_calling: None
) -> AsyncIterator[httpx.AsyncClient]:
    verifier = StaticVerifier({EDITOR: an_editor(), READER: a_reader()})
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        audit=audit_log,
        verifier=verifier,
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def as_(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    response = await client.post(
        "/elements",
        json={"element_type": "node", "name": name},
        headers=as_(EDITOR),
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestReadingTheTrail:
    async def test_a_write_over_http_is_in_the_trail_with_its_door_and_its_diff(
        self, client: httpx.AsyncClient
    ) -> None:
        element = await make(client, "db-01")
        await client.patch(
            f"/elements/{element['id']}", json={"name": "db-02"}, headers=as_(EDITOR)
        )

        page = (await client.get("/audit", headers=as_(READER))).json()

        assert [e["action"] for e in page["items"]] == ["element.updated", "element.created"]
        updated = page["items"][0]
        assert updated["channel"] == "http"
        assert updated["actor_name"] == "editor"
        assert updated["entity_id"] == element["id"]
        assert [(c["field"], c["before"], c["after"]) for c in updated["changes"]] == [
            ("name", "db-01", "db-02")
        ]

    async def test_the_history_of_an_element_is_the_audit_filtered_to_it(
        self, client: httpx.AsyncClient
    ) -> None:
        first, second = await make(client, "a"), await make(client, "b")

        history = (await client.get(f"/elements/{first['id']}/history", headers=as_(READER))).json()

        assert [e["entity_id"] for e in history["items"]] == [first["id"]]
        assert second["id"] not in str(history)

    async def test_pagination_is_stable_and_a_forged_cursor_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        for name in "abcde":
            await make(client, name)

        collected: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            page = (await client.get("/audit", params=params, headers=as_(READER))).json()
            collected.extend(e["id"] for e in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        forged = await client.get("/audit", params={"cursor": "garbage!"}, headers=as_(READER))

        assert len(collected) == len(set(collected)) == 5
        assert forged.status_code == 422
        assert forged.json()["error"] == "invalid_cursor"

    async def test_moments_without_a_zone_are_read_as_utc_and_bounds_are_enforced(
        self, client: httpx.AsyncClient
    ) -> None:
        await make(client, "a")

        naive = await client.get(
            "/audit", params={"since": "2000-01-01T00:00:00"}, headers=as_(READER)
        )
        too_big = await client.get("/audit", params={"limit": 201}, headers=as_(READER))
        unknown = await client.get(
            "/audit", params={"action": "element.exploded"}, headers=as_(READER)
        )

        assert naive.status_code == 200 and len(naive.json()["items"]) == 1
        assert too_big.status_code == 422
        assert unknown.status_code == 422

    async def test_reading_needs_a_token(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/audit")).status_code == 401


class TestHostileFilters:
    """What a caller can type into a URL is a refusal, never a 500 and never SQL."""

    @pytest.mark.parametrize(
        "params",
        [
            {"actor": "a\x00b"},
            {"request_id": "r\x00"},
            {"actor": "x" * 201},
            {"request_id": "x" * 129},
            {"cursor": "x" * 513},
            {"entity_id": "not-a-uuid"},
            {"entity_kind": "galaxy"},
            {"limit": 0},
            {"since": "yesterday"},
            {"since": "0001-01-01T00:00:00+14:00"},
            {"until": "9999-12-31T23:59:59-14:00"},
        ],
    )
    async def test_a_bad_value_is_a_422(
        self, client: httpx.AsyncClient, params: dict[str, str | int]
    ) -> None:
        response = await client.get("/audit", params=params, headers=as_(READER))

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(
        "cursor",
        [
            "",
            "e30",  # {}
            "WyIwMDAxLTAxLTAxVDAwOjAwOjAwKzE0OjAwIiwgIjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCJd",
            "WyIyMDI2LTEwLTEwIiwgNDJd",  # an integer where a uuid goes
        ],
    )
    async def test_a_forged_cursor_is_invalid_cursor(
        self, client: httpx.AsyncClient, cursor: str
    ) -> None:
        response = await client.get("/audit", params={"cursor": cursor}, headers=as_(READER))

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_cursor"

    async def test_a_quote_is_only_text(self, client: httpx.AsyncClient) -> None:
        await make(client, "a")

        response = await client.get("/audit", params={"actor": "x' OR '1'='1"}, headers=as_(READER))

        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_the_history_of_an_element_is_a_get_only_and_needs_a_token(
        self, client: httpx.AsyncClient
    ) -> None:
        element = await make(client, "a")
        path = f"/elements/{element['id']}/history"

        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, params={"limit": 0}, headers=as_(READER))).status_code == 422
        assert (await client.get("/elements/nope/history", headers=as_(READER))).status_code == 422

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    async def test_nothing_writes_to_the_trail_over_http(
        self, client: httpx.AsyncClient, method: str
    ) -> None:
        response = await client.request(method, "/audit", json={}, headers=as_(EDITOR))

        assert response.status_code == 405
