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
from uuid import uuid4

import httpx
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ea.core.config import Settings
from ea.domain.ports import ElementFilter
from ea.main import create_app
from ea.mcp import MCP_PATH
from ea.services.architecture import ArchitectureService
from ea.services.indexing import DocumentIndexer
from tests.conftest import FakeEmbedder, InMemoryDocuments

#: The transport turns on DNS-rebinding protection when it is served on a
#: loopback host, which checks the `Host` header against `127.0.0.1:*` and
#: friends. `http://test`, the base URL the REST suite uses, is rejected by it —
#: correctly — and so is a `127.0.0.1` carrying no port, hence the explicit one.
BASE_URL = "http://127.0.0.1:8000"


def an_app(service: ArchitectureService, **overrides: Any) -> Any:
    """The app over the graph double, with every other store stated shut.

    These tests enter the real lifespan, and the settings defaults open
    PostgreSQL and the embedding service on the cluster — correctly, for a
    deployment. Left implicit here, they made this suite wait on a timeout off
    the LAN, and talk to the shared database on it.
    """
    settings: dict[str, Any] = {
        "debug": True,
        "postgres_enabled": False,
        "embeddings_enabled": False,
        **overrides,
    }
    return create_app(Settings(**settings), architecture_service=service)


def an_app_with_documents(service: ArchitectureService, documents: InMemoryDocuments) -> Any:
    """The same app with every store doubled — nothing here leaves the process.

    `postgres_enabled=False` so the lifespan opens no engine; the document
    service is built from the injected repository instead, which is exactly the
    seam `create_app` documents. The indexer is injected for the same reason:
    given none, the lifespan builds the real embedding client and probes it.
    """
    return create_app(
        Settings(debug=True, postgres_enabled=False),
        architecture_service=service,
        documents=documents,  # type: ignore[arg-type]
        indexer=DocumentIndexer(FakeEmbedder()),
    )


@asynccontextmanager
async def agent_over(app: Any) -> AsyncIterator[ClientSession]:
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
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as http,
            streamable_http_client(f"{BASE_URL}{MCP_PATH}", http_client=http) as (read, write, *_),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


def agent(service: ArchitectureService) -> Any:
    """The common case: an agent talking to the app over the graph double."""
    return agent_over(an_app(service))


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
class TestTheDocumentTools:
    """The half of the catalogue that lives in PostgreSQL, reached over `/mcp`.

    `tests/unit/test_mcp_server.py` covers what these tools answer. What only
    assembly can prove is that `_mount_mcp` looked the document service up on
    the application at all — the tools are built before the lifespan creates
    it, so a lookup done at build time would have captured nothing.
    """

    async def test_an_agent_attaches_a_document_and_reads_it_back(
        self, service: ArchitectureService, documents: InMemoryDocuments
    ) -> None:
        async with agent_over(an_app_with_documents(service, documents)) as session:
            created = await session.call_tool(
                "create_element", {"element_type": "application_component", "name": "Billing"}
            )
            assert not created.is_error, created.content

            attached = await session.call_tool(
                "attach_document",
                {
                    "element_id": created.structured_content["id"],
                    "filename": "runbook.md",
                    "content": "# Runbook\n",
                },
            )
            assert not attached.is_error, attached.content

            read = await session.call_tool(
                "read_document", {"document_id": attached.structured_content["id"]}
            )

        assert read.structured_content["content"] == "# Runbook\n"

    async def test_a_client_is_told_that_discarding_a_document_destroys_it(
        self, service: ArchitectureService, documents: InMemoryDocuments
    ) -> None:
        """The annotation is what a client shows the person approving the call."""
        async with agent_over(an_app_with_documents(service, documents)) as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}

        assert tools["discard_document"].annotations.destructive_hint is True
        assert tools["list_documents"].annotations.read_only_hint is True

    async def test_with_the_relational_store_shut_the_tools_are_offered_and_fail(
        self, service: ArchitectureService
    ) -> None:
        """The deliberate asymmetry: the tool list is the adapter's, not the
        deployment's, exactly as `/documents` stays routed and answers a 500.
        A shut store is a misconfiguration — see `docs/adr/0018`."""
        app = create_app(Settings(debug=True, postgres_enabled=False), architecture_service=service)
        async with agent_over(app) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            result = await session.call_tool("list_documents", {"element_id": str(uuid4())})

        assert "list_documents" in names
        assert result.is_error


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

    async def test_a_foreign_host_header_is_refused_though_the_api_binds_the_world(
        self, service: ArchitectureService
    ) -> None:
        """Regression: the allowlist must not be derived from the bind address.

        The SDK turns DNS-rebinding protection on by itself only for a loopback
        `host`, so passing it `EA_HOST` switched the protection off the day that
        became `0.0.0.0` — leaving an unauthenticated write path onto the graph
        answering any `Host` a browser could be tricked into sending.
        """
        app = an_app(service, host="0.0.0.0")

        async with app.router.lifespan_context(app):
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(
                transport=transport, base_url="http://evil.example:8000"
            ) as http:
                response = await http.post(
                    MCP_PATH,
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    headers={"accept": "application/json, text/event-stream"},
                )

        assert response.status_code == 421


#: A JSON-RPC `initialize`, the first thing any MCP client sends.
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0"},
    },
}


async def initialize_from(app: Any, peer: str, *, host: str = BASE_URL) -> httpx.Response:
    """POST an `initialize` to `/mcp` as if the TCP connection came from `peer`.

    `ASGITransport(client=...)` is what sets `scope["client"]` — the address
    uvicorn reports for the socket, and the one thing about a caller the caller
    does not write itself.
    """
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=(peer, 50000))
        async with httpx.AsyncClient(transport=transport, base_url=host) as client:
            return await client.post(
                MCP_PATH,
                json=INITIALIZE,
                headers={"accept": "application/json, text/event-stream"},
            )


@pytest.mark.asyncio
class TestWhoIsServed:
    """Until auth exists `/mcp` answers this machine only — docs/adr/0023.

    The `Host` allowlist above is a defence against DNS rebinding, i.e. against
    a *browser* tricked into calling us. It does nothing against a script on
    the LAN, which writes `Host: localhost:8000` itself; the test below that
    does exactly that is the reason this class exists.
    """

    @pytest.mark.parametrize("peer", ["127.0.0.1", "::1"])
    async def test_a_client_on_this_machine_is_served(
        self, service: ArchitectureService, peer: str
    ) -> None:
        """What `.mcp.json` does: Claude Code on the same Mac, at 127.0.0.1."""
        response = await initialize_from(an_app(service), peer)

        assert response.status_code == 200

    async def test_a_client_on_the_lan_is_refused_whatever_host_it_claims(
        self, service: ArchitectureService
    ) -> None:
        """The regression: a forged loopback `Host` got a remote script served."""
        response = await initialize_from(
            an_app(service), "192.168.1.40", host="http://localhost:8000"
        )

        assert response.status_code == 403
        assert response.json()["error"] == "remote_client_refused"
        assert "EA_MCP_ALLOW_REMOTE_CLIENTS" in response.json()["detail"]

    async def test_the_refusal_touches_nothing(self, service: ArchitectureService) -> None:
        """Refused before the transport: no session, no tool, no write."""
        app = an_app(service)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=("192.168.1.40", 50000))
            async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
                response = await client.post(
                    MCP_PATH,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "create_element",
                            "arguments": {"element_type": "node", "name": "intruder"},
                        },
                    },
                    headers={"accept": "application/json, text/event-stream"},
                )

        assert response.status_code == 403
        assert not await service.list_elements(ElementFilter())

    async def test_remote_clients_are_served_once_the_deployment_opts_in(
        self, service: ArchitectureService
    ) -> None:
        app = an_app(service, mcp_allow_remote_clients=True)

        response = await initialize_from(app, "192.168.1.40")

        assert response.status_code == 200

    async def test_the_rest_api_is_not_narrowed_by_it(self, service: ArchitectureService) -> None:
        """The SPA is used from other machines; only the agent path is loopback."""
        transport = httpx.ASGITransport(app=an_app(service), client=("192.168.1.40", 50000))
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            response = await client.get("/health")

        assert response.status_code == 200
