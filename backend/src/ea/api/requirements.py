"""The requirements coverage endpoint.

It reads the same graph `/elements` serves: a requirement is an element and a
test or a design realizes it (`docs/adr/0067`). The route adds no store, and
the rates are the server's answer: the SPA shows them and never recomputes one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ea.api.dependencies import Requirements
from ea.api.schemas import CoverageFilter, CoverageRead, ErrorResponse

router = APIRouter(prefix="/requirements", tags=["requirements"])


@router.get(
    "/coverage",
    response_model=CoverageRead,
    responses={422: {"model": ErrorResponse}},
)
async def read_coverage(
    service: Requirements,
    status: Annotated[CoverageFilter | None, Query()] = None,
    priority: Annotated[CoverageFilter | None, Query()] = None,
) -> CoverageRead:
    """Which requirements are realized by a design and tested, and which are not.

    `status` and `priority` narrow the requirements listed and counted. The
    rates leave out rejected requirements and are `null` when none is left.
    """
    return CoverageRead.of(await service.coverage(status=status, priority=priority))
