"""The REST API refuses whoever cannot show a token, and writes for editors only."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

from ea.core.config import Settings
from ea.main import create_app
from ea.repositories.keycloak import AuthServiceError
from ea.services.architecture import ArchitectureService
from tests.conftest import StaticVerifier, a_reader, an_editor

PUBLIC = {"/health"}
EDITOR, READER = "editor-token", "reader-token"


def api_routes(routes: Iterable[Any]) -> Iterator[APIRoute]:
    """Every `APIRoute` reachable from `routes`, however deep `include_router` nested it.

    FastAPI wraps each `include_router()` call in a router-level object that
    carries its `APIRoute`s under `.original_router.routes` rather than
    flattening them into the parent's `.routes` — so a plain
    `isinstance(route, APIRoute)` walk over `app.routes` alone finds none of
    the routes this suite means to check.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        else:
            nested = getattr(getattr(route, "original_router", None), "routes", None)
            if nested is not None:
                yield from api_routes(nested)


def an_app(service: ArchitectureService):  # type: ignore[no-untyped-def]
    verifier = StaticVerifier({EDITOR: an_editor(), READER: a_reader()})
    return create_app(
        Settings(debug=True, auth_enabled=True), architecture_service=service, verifier=verifier
    )


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, nobody_calling: None
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=an_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_every_route_but_health_wants_a_token(
    service: ArchitectureService, client: httpx.AsyncClient
) -> None:
    routes = list(api_routes(an_app(service).routes))
    assert routes, "the route walk must actually find the routers this app includes"
    for route in routes:
        if route.path in PUBLIC:
            continue
        path = re.sub(r"\{[^}]+\}", str(uuid4()), route.path)
        for method in route.methods:
            response = await client.request(method, path)
            assert response.status_code == 401, f"{method} {route.path} -> {response.status_code}"
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.json()["error"] == "unauthenticated"


@pytest.mark.asyncio
async def test_health_stays_public(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_an_invalid_token_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/elements", headers=bearer("forged"))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_reader_reads(client: httpx.AsyncClient) -> None:
    assert (await client.get("/elements", headers=bearer(READER))).status_code == 200


@pytest.mark.asyncio
async def test_a_reader_cannot_write(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/elements",
        json={"element_type": "application_component", "name": "Billing"},
        headers=bearer(READER),
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.asyncio
async def test_an_editor_writes(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/elements",
        json={"element_type": "application_component", "name": "Billing"},
        headers=bearer(EDITOR),
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_me_says_who_and_whether_they_may_write(client: httpx.AsyncClient) -> None:
    assert (await client.get("/me", headers=bearer(READER))).json() == {
        "username": "reader",
        "can_write": False,
    }
    assert (await client.get("/me", headers=bearer(EDITOR))).json() == {
        "username": "editor",
        "can_write": True,
    }


@pytest.mark.asyncio
async def test_with_auth_off_the_local_developer_calls(
    service: ArchitectureService, nobody_calling: None
) -> None:
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/me")).json() == {
            "username": "local-developer",
            "can_write": True,
        }


class UnreachableKeycloak:
    """Stands in for a `JwtVerifier` whose JWKS fetch failed mid-request."""

    async def verify(self, token: str) -> object:
        raise AuthServiceError("cannot read the signing keys")


@pytest.mark.asyncio
async def test_keycloak_being_down_is_a_500_not_a_401(
    service: ArchitectureService, nobody_calling: None
) -> None:
    """`AuthServiceError` is nobody's fault about the request, so it must not
    be mistaken for a rejected token — see `ea/api/errors.py`."""
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        verifier=UnreachableKeycloak(),  # type: ignore[arg-type]
    )
    # `_handle_unexpected_error` re-raises after answering, so the traceback
    # still reaches the ASGI server's log (see `ea/api/errors.py`) — which
    # `raise_app_exceptions=False` is what lets this client see the response
    # instead of that re-raise.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/elements", headers=bearer("whatever"))

    assert response.status_code == 500
    assert "www-authenticate" not in response.headers
    assert response.json() == {
        "error": "internal_error",
        "detail": "The server failed to answer this request.",
    }
