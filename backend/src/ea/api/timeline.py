"""The time endpoints: the plateaus, the gap between two of them, the roadmap.

Reads compute on demand and store nothing; the one write draws a `gap` element
through `ArchitectureService` in a single transaction, so every rule of the model
applies to it (docs/adr/0061). The dates themselves are written with the ordinary
`PATCH /elements/{id}`: `valid_from` and `valid_until` are properties (docs/adr/0059).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from ea.api.dependencies import Packages, Timeline
from ea.api.schemas import (
    ElementRead,
    ErrorResponse,
    GapCreate,
    PlateauGapRead,
    PlateauRead,
    RoadmapRead,
)

router = APIRouter(tags=["timeline"])

_ERROR = {"model": ErrorResponse}
READ_REFUSED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}
WRITE_REFUSED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    **READ_REFUSED,
    status.HTTP_409_CONFLICT: _ERROR,
}
ONLY_422: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR
}


@router.get("/plateaus", response_model=list[PlateauRead], responses=ONLY_422)
async def list_plateaus(service: Timeline) -> list[PlateauRead]:
    """Every plateau, earliest first (a plateau with no start last), with its size."""
    return [PlateauRead.of(summary) for summary in await service.list_plateaus()]


@router.get("/plateaus/gap", response_model=PlateauGapRead, responses=READ_REFUSED)
async def read_plateau_gap(
    service: Timeline,
    packages: Packages,
    a: Annotated[UUID, Query(description="The plateau to start from.")],
    b: Annotated[UUID, Query(description="The plateau to reach.")],
) -> PlateauGapRead:
    """What leaves (`only_in_a`), arrives (`only_in_b`) and stays (`common`) from `a` to `b`.

    A plateau contains what it composes or aggregates. `gaps` lists the gap elements
    already drawn between the two.
    """
    return PlateauGapRead.of(await service.plateau_gap(a, b), freezes=await packages.freezes())


@router.post(
    "/plateaus/gap",
    response_model=ElementRead,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_REFUSED,
)
async def create_plateau_gap(
    payload: GapCreate, service: Timeline, packages: Packages
) -> ElementRead:
    """Draw the gap between two plateaus: a `gap` element associated to both."""
    gap = await service.create_gap(payload.a, payload.b, payload.name)
    return ElementRead.of(gap, freezes=await packages.freezes())


@router.get("/roadmap", response_model=RoadmapRead, responses=ONLY_422)
async def read_roadmap(
    service: Timeline,
    window_from: Annotated[
        date | None, Query(alias="from", description="First day shown (YYYY-MM-DD).")
    ] = None,
    window_to: Annotated[
        date | None, Query(alias="to", description="Last day shown; at most 20 years after `from`.")
    ] = None,
) -> RoadmapRead:
    """Plateaus, work packages and deliverables with their dates, and the links between them.

    The window defaults to the bounds of the data. What has no date is returned too,
    flagged `dated: false`, for the SPA to list apart.
    """
    return RoadmapRead.of(await service.roadmap(window_from, window_to))
