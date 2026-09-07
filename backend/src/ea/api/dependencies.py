"""What the routers ask for, and where it comes from.

The service is built once during the application lifespan and handed out here,
so a router never sees the driver or the repository.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ea.services.architecture import ArchitectureService


def get_architecture_service(request: Request) -> ArchitectureService:
    service: ArchitectureService | None = getattr(request.app.state, "architecture_service", None)
    if service is None:  # pragma: no cover - a misassembled app, not a request error
        msg = "the architecture service was not attached to the application"
        raise RuntimeError(msg)
    return service


Architecture = Annotated[ArchitectureService, Depends(get_architecture_service)]
