"""One line per request, and the id that ties every other line to it.

Nothing configured logging before this (docs/adr/0021), so the API could not be
asked what it had just done: uvicorn's access line knows the status and not the
duration, and knows nothing at all about the twelve lines our own code logged
while serving that request.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from ea.api.middleware import REQUEST_ID_HEADER, RequestLogging
from ea.core.config import Settings
from ea.core.logging import REQUESTS_LOGGER, request_id
from ea.main import create_app

#: What `request_id.get()` read inside the route, request by request.
served: list[str] = []


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """An app with the middleware and three routes to aim at."""
    app = FastAPI()
    served.clear()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/elements")
    async def elements() -> list[str]:
        # What a call site would see: it logs whatever it has to say, and the
        # filter on the handler stamps this id onto the record for it.
        served.append(request_id.get())
        return []

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("no")

    app.add_middleware(RequestLogging)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as opened:
        yield opened


def lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == REQUESTS_LOGGER]


@pytest.mark.asyncio
class TestTheAccessLine:
    async def test_one_line_per_request_carrying_what_it_did(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            await client.get("/elements")

        (line,) = lines(caplog)
        assert line.levelno == logging.INFO
        assert line.method == "GET"  # type: ignore[attr-defined]
        assert line.path == "/elements"  # type: ignore[attr-defined]
        assert line.status == 200  # type: ignore[attr-defined]
        assert line.duration_ms >= 0  # type: ignore[attr-defined]

    async def test_a_query_string_is_kept_because_it_is_the_question_asked(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            await client.get("/elements?layer=application&limit=10")

        (line,) = lines(caplog)
        assert line.query == "layer=application&limit=10"  # type: ignore[attr-defined]

    async def test_a_liveness_probe_does_not_fill_the_log(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`/health` answering 200 says nothing; a probe polls it forever."""
        with caplog.at_level(logging.DEBUG):
            await client.get("/health")

        (line,) = lines(caplog)
        assert line.levelno == logging.DEBUG

    async def test_a_failure_is_logged_as_one(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            await client.get("/boom")

        (line,) = lines(caplog)
        assert line.levelno == logging.ERROR
        assert line.status == 500  # type: ignore[attr-defined]

    async def test_a_rejected_request_is_a_warning_and_not_a_fault(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            await client.get("/nowhere")

        (line,) = lines(caplog)
        assert line.levelno == logging.WARNING
        assert line.status == 404  # type: ignore[attr-defined]


@pytest.mark.asyncio
class TestTheRequestId:
    async def test_the_answer_says_which_request_it_was(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/elements")

        assert response.headers[REQUEST_ID_HEADER]

    async def test_an_id_the_caller_brought_is_kept(self, client: httpx.AsyncClient) -> None:
        """A caller correlating its own trace with ours must not be renamed."""
        response = await client.get("/elements", headers={REQUEST_ID_HEADER: "caller-42"})

        assert response.headers[REQUEST_ID_HEADER] == "caller-42"

    async def test_a_forged_id_is_not_echoed_back(self, client: httpx.AsyncClient) -> None:
        """It lands in a log line, so it is bounded and it is not free text."""
        response = await client.get("/elements", headers={REQUEST_ID_HEADER: "a b\nc" * 50})

        assert response.headers[REQUEST_ID_HEADER] != "a b\nc" * 50

    async def test_every_line_logged_while_serving_names_the_request(
        self, client: httpx.AsyncClient
    ) -> None:
        """This is what the id is for: the twelve lines around the access line."""
        response = await client.get("/elements", headers={REQUEST_ID_HEADER: "caller-42"})

        assert response.status_code == 200
        assert served == ["caller-42"]

    async def test_the_id_does_not_leak_out_of_the_request(self, client: httpx.AsyncClient) -> None:
        await client.get("/elements")

        assert request_id.get() == "-"


@pytest.mark.asyncio
async def test_the_real_app_carries_the_middleware_and_exposes_the_header() -> None:
    """A browser can only read the id if CORS says it may."""
    app = create_app(Settings(debug=True))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert response.headers[REQUEST_ID_HEADER]
    assert REQUEST_ID_HEADER.lower() in response.headers["access-control-expose-headers"].lower()
