"""FastAPI application factory and the `fastapi dev` / `uvicorn` entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ea.api.architecture import router as architecture_router
from ea.api.errors import register_error_handlers
from ea.api.health import router as health_router
from ea.api.metamodel import router as metamodel_router
from ea.core.config import Settings, get_settings
from ea.db.neo4j import create_driver, prepare_database
from ea.repositories.archimate_graph import Neo4jArchitectureRepository
from ea.services.architecture import ArchitectureService

logger = logging.getLogger(__name__)


def _graph_lifespan(
    settings: Settings,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Open one driver for the process, apply the schema, close it on shutdown.

    The driver owns a connection pool, so it is built once and shared. Applying
    the schema here means a fresh database becomes usable by starting the app —
    the graph has no `alembic upgrade` step; see `docs/adr/0004`.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        driver = create_driver(settings)
        try:
            await prepare_database(driver, database=settings.neo4j_database)
            repository = Neo4jArchitectureRepository(driver, database=settings.neo4j_database)
            app.state.architecture_service = ArchitectureService(repository)
            yield
        finally:
            await driver.close()

    return lifespan


def create_app(
    settings: Settings | None = None,
    *,
    architecture_service: ArchitectureService | None = None,
) -> FastAPI:
    """Assemble the application.

    Taking `settings` as an argument keeps the app testable without touching
    the process environment. Passing `architecture_service` swaps the graph for
    a double, so an API test never needs a running database — and, conversely,
    an app built without one opens the driver on startup.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=None if architecture_service else _graph_lifespan(settings),
    )
    app.state.settings = settings
    if architecture_service is not None:
        app.state.architecture_service = architecture_service

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
