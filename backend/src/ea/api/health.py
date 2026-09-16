"""Liveness endpoint — the cheapest proof that the stack is up, and what is missing from it.

It is the one route that carries no `Authenticated` dependency and asks no
store a question, so it answers as long as the process runs. Since
docs/adr/0037 the process also runs when a store that serves one section is
out of reach, which is exactly when somebody needs to be told: `degraded` names
those sections, and `status` says `degraded` rather than `ok`.

**It stays a 200 either way.** This is the container's `HEALTHCHECK` and the
SPA's first call: a 503 here would mark a deployment that is serving the
catalogue perfectly well as unhealthy. Liveness is not readiness.

**It names sections, never reasons.** `files` and `search` say which part of
the application refuses and nothing about which host, bucket or model is at
fault — that is in the log, under `action=subsystem_degraded`, where an
unauthenticated caller cannot read it.
"""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Declared so no internal field can leak into the response."""

    status: Literal["ok", "degraded"] = Field(
        description="`degraded` when a store that serves one section failed its boot probe."
    )
    degraded: list[str] = Field(
        default_factory=list,
        description="The sections that will refuse, e.g. `files`, `search`. Empty when `ok`.",
    )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    # `getattr` because an app driven without its lifespan — an API test over
    # `ASGITransport` — has opened nothing and so has degraded nothing.
    degraded = list(getattr(request.app.state, "degraded", ()))
    return HealthResponse(status="degraded" if degraded else "ok", degraded=degraded)
