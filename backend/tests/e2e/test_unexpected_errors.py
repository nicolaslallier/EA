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

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository

#: What the exception says about the machine — the kind of detail a traceback
#: page would print, and a client must never read.
INTERNALS = "/Users/somebody/secret/repository.py: bolt://neo4j:hunter2@graph"


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
    app = create_app(Settings(debug=True), architecture_service=service)
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
