"""What the routers ask for, and where it comes from.

The service is built once during the application lifespan and handed out here,
so a router never sees the driver or the repository.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Request

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
