"""The endpoints of the saved diagrams — ArchiMate views of the graph.

A diagram records which elements are drawn and where (docs/adr/0031). A link
drawn on one is created with `POST /relationships`, like any other: there is
no diagram-specific way to change the graph.
"""

from __future__ import annotations

from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import Diagrams
from ea.api.schemas import (
    AsOf,
    DiagramCreate,
    DiagramLayout,
    DiagramRead,
    DiagramSummaryRead,
    DiagramUpdate,
    ErrorResponse,
)
from ea.domain.packages import UNSET

router = APIRouter(tags=["diagrams"])

#: The revision a save returns, which the SPA keeps for the next one.
REVISION_HEADER: Final = "X-Diagram-Revision"

_ERROR = {"model": ErrorResponse}
NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_404_NOT_FOUND: _ERROR}
CONFLICT: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_409_CONFLICT: _ERROR}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
}
LAYOUT_REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}


@router.get("/diagrams", response_model=list[DiagramSummaryRead])
async def list_diagrams(
    diagrams: Diagrams,
    package: Annotated[UUID | None, Query()] = None,
    include_subpackages: Annotated[bool, Query()] = True,
    root_only: Annotated[bool, Query()] = False,
) -> list[DiagramSummaryRead]:
    """Every saved diagram, sorted by name — or only those filed in a package."""
    listed = await diagrams.list_diagrams(
        package_id=package, include_subpackages=include_subpackages, root_only=root_only
    )
    return [DiagramSummaryRead.of(diagram) for diagram in listed]


@router.post(
    "/diagrams",
    response_model=DiagramSummaryRead,
    status_code=status.HTTP_201_CREATED,
    responses=REJECTED,
)
async def create_diagram(body: DiagramCreate, diagrams: Diagrams) -> DiagramSummaryRead:
    """A new, empty diagram. Its name must not be taken."""
    created = await diagrams.create(
        name=body.name, description=body.description, package_id=body.package_id
    )
    return DiagramSummaryRead.of(created)


@router.get("/diagrams/{diagram_id}", response_model=DiagramRead, responses=NOT_FOUND)
async def read_diagram(
    diagram_id: UUID, diagrams: Diagrams, as_of: Annotated[AsOf | None, Query()] = None
) -> DiagramRead:
    """A diagram with its boxes, the elements they show and the links between them.

    `as_of` removes nothing: it lists, in `inactive_*_ids`, what is out of period that day.
    """
    return DiagramRead.of(await diagrams.open(diagram_id, as_of=as_of))


@router.patch("/diagrams/{diagram_id}", response_model=DiagramSummaryRead, responses=REJECTED)
async def update_diagram(
    diagram_id: UUID, body: DiagramUpdate, diagrams: Diagrams
) -> DiagramSummaryRead:
    package = body.package_id if "package_id" in body.model_fields_set else UNSET
    updated = await diagrams.update(
        diagram_id, name=body.name, description=body.description, package_id=package
    )
    return DiagramSummaryRead.of(updated)


@router.delete(
    "/diagrams/{diagram_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_diagram(diagram_id: UUID, diagrams: Diagrams) -> Response:
    """Delete a diagram. The elements it showed are untouched."""
    await diagrams.delete(diagram_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/diagrams/{diagram_id}/layout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=LAYOUT_REJECTED,
)
async def replace_diagram_layout(
    diagram_id: UUID, body: DiagramLayout, diagrams: Diagrams
) -> Response:
    """Replace the whole scene of a diagram at once.

    `revision` is the one the client read; if somebody saved since, nothing is
    written and the answer is a 409 `diagram_revision_conflict`. Otherwise the
    new revision comes back in `X-Diagram-Revision`. Each element must exist
    and appear once, and a route may only name a link joining two placed
    elements; otherwise nothing is written.
    """
    revision = await diagrams.replace_layout(
        diagram_id,
        revision=body.revision,
        nodes=[node.placed() for node in body.nodes],
        objects=[item.placed() for item in body.objects],
        edges=[edge.placed() for edge in body.edges],
    )
    return Response(
        status_code=status.HTTP_204_NO_CONTENT, headers={REVISION_HEADER: str(revision)}
    )
