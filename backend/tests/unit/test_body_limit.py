"""The body ceiling is enforced as the bytes arrive, not from a header."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Request

from ea.api.body_limit import BODY_ENVELOPE_MARGIN, LimitingRequestBodies
from ea.core.config import Settings

pytestmark = pytest.mark.asyncio
SEEN: list[int] = []
LIMIT = 1000 + BODY_ENVELOPE_MARGIN


def make_app(limit: int = LIMIT) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(debug=True, auth_enabled=False)  # the local developer calls

    @app.post("/exchanges/imports")
    async def imports(request: Request) -> dict[str, int]:
        body = await request.body()
        SEEN.append(len(body))
        return {"size": len(body)}

    @app.post("/files")
    async def files(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    app.add_middleware(LimitingRequestBodies, path_suffix="/exchanges/imports", limit=limit)
    return app


async def post(app: FastAPI, path: str, content: bytes | AsyncIterator[bytes]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, content=content)


async def test_a_body_under_the_limit_passes() -> None:
    response = await post(make_app(), "/exchanges/imports", b"x" * 500)

    assert response.status_code == 200 and response.json() == {"size": 500}


async def test_a_body_exactly_at_the_limit_passes() -> None:
    response = await post(make_app(), "/exchanges/imports", b"x" * LIMIT)

    assert response.status_code == 200


async def test_a_declared_length_over_the_limit_is_refused_before_reading() -> None:
    SEEN.clear()

    response = await post(make_app(), "/exchanges/imports", b"x" * (LIMIT + 1))

    assert response.status_code == 413
    assert response.json()["error"] == "file_too_large"
    assert SEEN == []


async def test_a_chunked_body_is_cut_as_it_arrives_without_trusting_any_header() -> None:
    SEEN.clear()
    received = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal received
        for _ in range(2_000):
            received += 1
            yield b"x" * 1000  # no Content-Length: the stream is `Transfer-Encoding: chunked`

    transport = httpx.ASGITransport(app=make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request = client.build_request("POST", "/exchanges/imports", content=chunks())
        assert "content-length" not in request.headers
        response = await client.send(request)

    assert response.status_code == 413
    assert response.json()["error"] == "file_too_large"
    assert SEEN == []  # the route never saw a body


async def test_the_ceiling_in_the_message_is_the_file_ceiling_not_the_envelope() -> None:
    response = await post(make_app(), "/exchanges/imports", b"x" * (LIMIT + 1))

    assert "1000 bytes" in response.json()["detail"]


async def test_other_paths_are_not_limited() -> None:
    response = await post(make_app(), "/files", b"x" * (LIMIT * 3))

    assert response.status_code == 200


async def test_a_get_on_the_same_path_is_not_limited() -> None:
    transport = httpx.ASGITransport(app=make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/exchanges/imports")

    assert response.status_code == 405  # the route's own answer, not a 413
