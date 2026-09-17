"""A failure nobody anticipated, as a client on the network sees it.

`api/errors.py` maps the failures we expect. This covers the rest — a driver
bug, a `KeyError` in a service — and the rule `CLAUDE.md` states for them:
the client is told something typed and generic, and the traceback goes to the
logs. The API binds every interface (docs/adr/0016), so whatever a 500 carries
is carried to anyone on the LAN.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import BaseModel
from starlette.types import Message, Receive, Scope, Send

from ea.api.errors import AnsweringUnexpectedFailures
from ea.api.middleware import REQUEST_ID_HEADER
from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository

#: What the exception says about the machine — the kind of detail a traceback
#: page would print, and a client must never read.
INTERNALS = "/Users/somebody/secret/repository.py: postgresql://ea:hunter2@db/ea"

#: The one origin `Settings` allows by default, and so the one a browser may
#: present here. A 500 has to carry the CORS headers like any other answer, or
#: the browser hides it and the SPA reports an unreachable backend instead.
ALLOWED_ORIGIN = "http://localhost:5173"


class StoredRow(BaseModel):
    """Stands in for a model a service builds from stored data.

    Its name and its field name are what must never reach a client:
    `pydantic.ValidationError` *is* a `ValueError`, so the handler that answers
    a rejected input with 422 would otherwise hand both over, with the value.
    """

    internal_column: str


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService,
    repository: InMemoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """The app as `.env.example` configures it — `EA_DEBUG=true` — over a graph
    that breaks in a way no handler anticipates."""

    async def broken(*_: object, **__: object) -> None:
        raise RuntimeError(INTERNALS)

    monkeypatch.setattr(repository, "get_element", broken)
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    # The server re-raises after answering, so the exception reaches the ASGI
    # server's log; the transport must not re-raise it into the test instead of
    # handing back the response the client actually receives.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
class TestAnUnexpectedFailure:
    async def test_it_is_a_500_in_the_api_s_own_envelope(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"/elements/{uuid4()}")

        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {
            "error": "internal_error",
            "detail": "The server failed to answer this request.",
        }

    async def test_it_carries_no_traceback_even_with_debug_on(
        self, client: httpx.AsyncClient
    ) -> None:
        """Regression: `debug=settings.debug` handed Starlette's traceback page
        to any client, and `.env.example` ships `EA_DEBUG=true`."""
        response = await client.get(f"/elements/{uuid4()}", headers={"accept": "text/html"})

        assert "Traceback" not in response.text
        assert "secret" not in response.text
        assert ".py" not in response.text

    async def test_a_browser_may_read_it(self, client: httpx.AsyncClient) -> None:
        """Regression: the envelope was sent by Starlette's `ServerErrorMiddleware`,
        which sits outside every middleware this app adds — so a 500 left without
        the CORS headers. The browser then blocked it, `fetch` rejected, and the
        SPA reported an unreachable backend for an API that had just answered."""
        response = await client.get(f"/elements/{uuid4()}", headers={"origin": ALLOWED_ORIGIN})

        assert response.status_code == 500
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN

    async def test_it_carries_the_request_id_of_the_request_that_failed(
        self, client: httpx.AsyncClient
    ) -> None:
        """The one answer that most needs joining to a log line had no id on it."""
        response = await client.get(
            f"/elements/{uuid4()}", headers={REQUEST_ID_HEADER: "a-failing-call"}
        )

        assert response.status_code == 500
        assert response.headers[REQUEST_ID_HEADER] == "a-failing-call"


@pytest_asyncio.fixture
async def rejecting_client(
    service: ArchitectureService,
    repository: InMemoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """A graph whose stored data no longer satisfies a model built from it."""

    async def mistyped(*_: object, **__: object) -> None:
        StoredRow(internal_column=1)  # type: ignore[arg-type]

    monkeypatch.setattr(repository, "get_element", mistyped)
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
class TestAValidationErrorFromInsideTheServer:
    """`pydantic.ValidationError` is a `ValueError`, and that is the trap.

    `api/errors.py` answers a `ValueError` with 422 and the message, because the
    domain raises one for a rejected input — a blank name, a property key that
    is not an identifier — written for a human and naming nothing internal. A
    `ValidationError` raised anywhere else says which model, which field and
    what the value was, which is the opposite.
    """

    async def test_it_is_the_internal_error_envelope(
        self, rejecting_client: httpx.AsyncClient
    ) -> None:
        response = await rejecting_client.get(f"/elements/{uuid4()}")

        assert response.status_code == 500
        assert response.json() == {
            "error": "internal_error",
            "detail": "The server failed to answer this request.",
        }

    async def test_it_names_neither_the_model_nor_the_field_nor_the_value(
        self, rejecting_client: httpx.AsyncClient
    ) -> None:
        response = await rejecting_client.get(f"/elements/{uuid4()}")

        assert "StoredRow" not in response.text
        assert "internal_column" not in response.text


async def _nothing(*_: object, **__: object) -> None:
    """A `receive`/`send` that is never meant to be called."""
    raise AssertionError("not expected to be called")


@pytest.mark.asyncio
class TestAFailureOnceTheAnswerHasStarted:
    """A download that dies halfway has already sent its status line.

    There is nothing left to replace it with, so the middleware steps aside and
    lets the failure travel: the connection breaks, which is the only honest
    thing left. Answering anyway would hand the client a JSON envelope under
    the `Content-Type` of the file it asked for — `/files/content` streams
    (`api/files.py`), so this is a real path and not a hypothesis.
    """

    async def test_the_envelope_does_not_replace_what_was_already_sent(self) -> None:
        sent: list[str] = []

        async def failing_stream(_: Scope, __: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"half", "more_body": True})
            raise RuntimeError(INTERNALS)

        async def record(message: Message) -> None:
            sent.append(str(message["type"]))

        guarded = AnsweringUnexpectedFailures(failing_stream)

        with pytest.raises(RuntimeError):
            await guarded(
                {"type": "http", "method": "GET", "path": "/files/content"}, _nothing, record
            )

        # The 200 and its one chunk — and no second `http.response.start`.
        assert sent == ["http.response.start", "http.response.body"]

    async def test_a_scope_that_is_not_http_passes_straight_through(self) -> None:
        """The lifespan and a websocket are not requests, and have no envelope."""
        seen: list[Scope] = []

        async def inner(scope: Scope, _: Receive, __: Send) -> None:
            seen.append(scope)

        await AnsweringUnexpectedFailures(inner)({"type": "lifespan"}, _nothing, _nothing)

        assert seen == [{"type": "lifespan"}]
