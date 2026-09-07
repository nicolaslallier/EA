"""FastAPI application factory and the `fastapi dev` / `uvicorn` entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings

from ea.api.architecture import router as architecture_router
from ea.api.dependencies import architecture_service_of, document_service_of
from ea.api.documents import router as documents_router
from ea.api.errors import register_error_handlers
from ea.api.health import router as health_router
from ea.api.metamodel import router as metamodel_router
from ea.core.config import Settings, get_settings
from ea.db.neo4j import create_driver, prepare_database
from ea.db.postgres import check_connectivity as check_relational_store
from ea.db.postgres import create_engine, create_session_factory
from ea.domain.ports import DocumentRepository
from ea.mcp import MCP_PATH, build_mcp_server
from ea.repositories.archimate_graph import Neo4jArchitectureRepository
from ea.repositories.document_store import PostgresDocumentRepository
from ea.services.architecture import ArchitectureService
from ea.services.documents import DocumentService

logger = logging.getLogger(__name__)


def _lifespan(
    settings: Settings,
    *,
    open_graph: bool,
    documents: DocumentRepository | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Start and stop everything the process owns, however it was assembled.

    Two things need a lifetime. The Neo4j driver owns a connection pool, so it
    is built once and shared; applying the schema here means a fresh database
    becomes usable by starting the app — the graph has no `alembic upgrade`
    step, see `docs/adr/0004`. The MCP transport keeps its sessions in a
    manager that has to be running before `/mcp` answers anything; it is picked
    up off `app.state`, where `_mount_mcp` left it, because the manager only
    exists once the app it is mounted on does.

    PostgreSQL is a third, and it is opened only when `postgres_enabled` says
    something stores anything there — no table does yet (docs/adr/0015), so the
    default is off and a machine that never ran `make pg-up` still boots.

    An `AsyncExitStack` composes them, which is why this is one lifespan rather
    than two: an app built with `architecture_service=` opens no driver but
    must still start the session manager, and before this it had no lifespan at
    all — `/mcp` would have accepted requests it could never answer.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            # PostgreSQL first: the markdown attached to an element lives here,
            # and the architecture service built below has to be handed the
            # repository that deletes it when the element goes.
            attachments = documents
            if settings.postgres_enabled:
                engine = create_engine(settings)
                stack.push_async_callback(engine.dispose)
                await check_relational_store(engine)
                app.state.db_sessions = create_session_factory(engine)
                if attachments is None:
                    attachments = PostgresDocumentRepository(app.state.db_sessions)
            if open_graph:
                driver = create_driver(settings)
                stack.push_async_callback(driver.close)
                await prepare_database(driver, database=settings.neo4j_database)
                repository = Neo4jArchitectureRepository(driver, database=settings.neo4j_database)
                app.state.architecture_service = ArchitectureService(
                    repository, attachments=attachments
                )
            if attachments is not None:
                app.state.document_service = DocumentService(
                    attachments, architecture_service_of(app)
                )
            sessions = getattr(app.state, "mcp_sessions", None)
            if sessions is not None:
                await stack.enter_async_context(sessions.run())
            yield

    return lifespan


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    """Which `Host` and `Origin` headers `/mcp` answers.

    The origins are derived from the hosts rather than configured twice: an
    entry is a host and an optional port, and a browser reaching it does so
    over one of the two schemes. Anything not on the list is a 421.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.mcp_allowed_hosts),
        allowed_origins=[
            f"{scheme}://{allowed}"
            for allowed in settings.mcp_allowed_hosts
            for scheme in ("http", "https")
        ],
    )


def _mount_mcp(app: FastAPI, settings: Settings) -> None:
    """Serve the MCP tools at `/mcp`, on the app that already serves the API.

    The SDK hands back a Starlette application whose single route is the
    transport. Its routes are spliced onto this app rather than `mount`ed,
    because `Mount("/mcp", ...)` matches only `/mcp/...`: a bare `POST /mcp` —
    the address every client is given — would be answered with a 307 redirect,
    and a client is not obliged to follow one. Splicing keeps the exact path.

    Two consequences worth knowing. The route is a Starlette `Route` and not an
    `APIRoute`, so it stays out of the OpenAPI schema — `/mcp` describes itself
    over MCP, and the generated TypeScript client neither sees it nor needs
    regenerating for it. And the sub-application's own lifespan is dropped,
    which is why its session manager is handed to `_lifespan` instead.

    Both services are looked up per call, off `app.state`, for the same reason:
    they are built by the lifespan and this runs while the app is still being
    assembled. A deployment with the relational store shut therefore serves the
    document tools and fails them one by one — the wiring fault the REST
    adapter answers with a 500, said in the other protocol.

    The transport security is stated rather than inferred. Given a `host`, the
    SDK enables DNS-rebinding protection *only* when that host is loopback — so
    handing it `settings.host` silently switched the protection off the day the
    API started binding every interface, on a path that writes to the graph
    without authentication. The allowlist is its own setting instead.
    """
    server = build_mcp_server(
        lambda: architecture_service_of(app),
        lambda: document_service_of(app),
        version=app.version,
    )
    transport = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=_transport_security(settings),
    )
    if transport.user_middleware:  # pragma: no cover - only auth adds any today
        msg = "the MCP transport now ships middleware that splicing its routes would drop"
        raise RuntimeError(msg)
    app.router.routes.extend(transport.routes)
    app.state.mcp_sessions = server.session_manager


def create_app(
    settings: Settings | None = None,
    *,
    architecture_service: ArchitectureService | None = None,
    documents: DocumentRepository | None = None,
) -> FastAPI:
    """Assemble the application.

    Taking `settings` as an argument keeps the app testable without touching
    the process environment. Passing `architecture_service` swaps the graph for
    a double, so an API test never needs a running database — and, conversely,
    an app built without one opens the driver on startup. `documents` does the
    same for the relational store: given one, the document endpoints answer
    without PostgreSQL; given none, the lifespan builds the real repository
    when `postgres_enabled` says the store is open.

    An injected pair is wired here rather than in the lifespan, because an API
    test drives the app through `ASGITransport` without ever starting it.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=_lifespan(settings, open_graph=architecture_service is None, documents=documents),
    )
    app.state.settings = settings
    if architecture_service is not None:
        app.state.architecture_service = architecture_service
        if documents is not None:
            app.state.document_service = DocumentService(documents, architecture_service)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(metamodel_router)
    app.include_router(architecture_router)
    app.include_router(documents_router)
    if settings.mcp_enabled:
        _mount_mcp(app, settings)
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Build `ea.main:app` on first access rather than at import.

    `uvicorn ea.main:app` resolves the attribute after importing the module, so
    this keeps the familiar entry point while making sure the environment is
    only read when the process actually intends to serve. Importing `ea.main`
    in a test no longer requires a fully configured deployment.
    """
    if name != "app":
        raise AttributeError(name)
    global _app
    if _app is None:
        _app = create_app()
    return _app
