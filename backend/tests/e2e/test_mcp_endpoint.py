"""The MCP adapter reached the way an agent reaches it: over HTTP, at `/mcp`.

`tests/unit/test_mcp_server.py` covers what the tools answer. This covers the
part that only fails in assembly — that the transport is mounted at exactly the
path clients are given, that its session manager is started by the application
lifespan, and that mounting it left the REST contract alone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ea.core.config import Settings
from ea.main import create_app
from ea.mcp import MCP_PATH
from ea.services.architecture import ArchitectureService

#: The transport turns on DNS-rebinding protection when it is served on a
#: loopback host, which checks the `Host` header against `127.0.0.1:*` and
#: friends. `http://test`, the base URL the REST suite uses, is rejected by it —
#: correctly — and so is a `127.0.0.1` carrying no port, hence the explicit one.
BASE_URL = "http://127.0.0.1:8000"


def an_app(service: ArchitectureService, **overrides: Any) -> Any:
    return create_app(Settings(debug=True, **overrides), architecture_service=service)


@asynccontextmanager
async def agent(service: ArchitectureService) -> AsyncIterator[ClientSession]:
    """A real MCP client speaking to the real app over an in-process transport.

    `httpx.ASGITransport` does not run the application lifespan, and the
    session manager lives there, so this enters it by hand — which is also the
    assertion that `create_app` puts it there at all.

    A helper rather than a fixture on purpose: the transport nests anyio task
    groups, and anyio refuses to unwind a cancel scope in a task other than the
    one that entered it. A yielding fixture is finalised in a different task
    from the test body, so the stack has to open and close inside one `async
    with`, in the test.
    """
    app = an_app(service)
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as http,
            streamable_http_client(f"{BASE_URL}{MCP_PATH}", http_client=http) as (read, write, *_),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


@pytest.mark.asyncio
class TestTheEndpoint:
    async def test_an_agent_can_list_the_tools(self, service: ArchitectureService) -> None:
        async with agent(service) as session:
            tools = (await session.list_tools()).tools

        assert {tool.name for tool in tools} >= {"create_element", "describe_metamodel"}

    async def test_the_server_tells_a_client_to_read_the_metamodel_first(
        self, service: ArchitectureService
    ) -> None:
        """The instructions are the cheapest way to stop an agent inventing a type."""
        async with agent(service) as session:
            result = await session.initialize()

        assert result.instructions is not None
        assert "describe_metamodel" in result.instructions

    async def test_an_element_created_through_a_tool_can_be_read_back_through_one(
        self, service: ArchitectureService
    ) -> None:
        async with agent(service) as session:
            created = await session.call_tool(
                "create_element", {"element_type": "application_component", "name": "Billing"}
            )
            assert not created.is_error, created.content

            read = await session.call_tool(
                "get_element", {"element_id": created.structured_content["id"]}
            )

        assert read.structured_content["name"] == "Billing"

    async def test_a_refused_call_comes_back_readable_rather_than_as_a_crash(
        self, service: ArchitectureService
    ) -> None:
        """`is_error` with the reason, so the model can correct itself and retry."""
        async with agent(service) as session:
            result = await session.call_tool(
                "create_element", {"element_type": "microservice", "name": "Billing"}
            )

        assert result.is_error
        assert "microservice" in str(result.content)


@pytest.mark.asyncio
class TestTheMounting:
    async def test_the_transport_answers_on_the_bare_path_without_a_redirect(
        self, service: ArchitectureService
    ) -> None:
        """A `Mount` would answer `POST /mcp` with a 307 to `/mcp/`; splicing does not."""
        app = an_app(service)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
                response = await client.post(
                    MCP_PATH,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "probe", "version": "0"},
                        },
                    },
                    headers={"accept": "application/json, text/event-stream"},
                )

        assert response.status_code == 200

    async def test_it_can_be_switched_off(self, service: ArchitectureService) -> None:
        """Until auth lands, a deployment that does not want an agent-facing
        write path on the graph turns it off — see `docs/adr/0014`."""
        app = an_app(service, mcp_enabled=False)

        assert MCP_PATH not in {getattr(route, "path", None) for route in app.router.routes}

    async def test_mounting_it_leaves_the_openapi_schema_alone(
        self, service: ArchitectureService
    ) -> None:
        """The transport is a Starlette route, not an `APIRoute`, so the
        generated TypeScript client neither sees it nor needs regenerating."""
        with_mcp = an_app(service).openapi()
        without_mcp = an_app(service, mcp_enabled=False).openapi()

        assert with_mcp == without_mcp
        assert MCP_PATH not in with_mcp["paths"]
