"""What the routers ask for, and where it comes from.

The service is built once during the application lifespan and handed out here,
so a router never sees the driver or the repository.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ea.services.architecture import ArchitectureService


def architecture_service_of(app: FastAPI) -> ArchitectureService:
    """The service the lifespan attached, or a clear failure saying it did not.

    Taken from the application rather than the request because the MCP adapter
    needs the same lookup and has no request to ask — see `main._mount_mcp`.
    """
    service: ArchitectureService | None = getattr(app.state, "architecture_service", None)
    if service is None:  # pragma: no cover - a misassembled app, not a request error
        msg = "the architecture service was not attached to the application"
        raise RuntimeError(msg)
    return service


def get_architecture_service(request: Request) -> ArchitectureService:
    return architecture_service_of(request.app)


Architecture = Annotated[ArchitectureService, Depends(get_architecture_service)]


def session_factory_of(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    """The factory the lifespan attached, or a clear failure saying it did not.

    Absent means `postgres_enabled` is off, which is the default until the
    first table exists — see docs/adr/0015.
    """
    factory: async_sessionmaker[AsyncSession] | None = getattr(app.state, "db_sessions", None)
    if factory is None:
        msg = "no relational session factory on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return factory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, closed when the response is done.

    It is not committed here. A transaction spans a use case, not an HTTP
    request, so `services/` opens and commits it — see `CLAUDE.md`.
    """
    async with session_factory_of(request.app)() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]
