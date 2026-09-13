"""The endpoints of the saved diagrams — ArchiMate views of the graph.

A diagram records which elements are drawn and where (docs/adr/0031). A link
drawn on one is created with `POST /relationships`, like any other: there is
no diagram-specific way to change the graph.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from ea.api.dependencies import Diagrams
from ea.api.schemas import (
    DiagramCreate,
    DiagramLayout,
    DiagramRead,
    DiagramSummaryRead,
    DiagramUpdate,
    ErrorResponse,
)

router = APIRouter(tags=["diagrams"])

_ERROR = {"model": ErrorResponse}
NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_404_NOT_FOUND: _ERROR}
CONFLICT: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_409_CONFLICT: _ERROR}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
}
LAYOUT_REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}


@router.get("/diagrams", response_model=list[DiagramSummaryRead])
async def list_diagrams(diagrams: Diagrams) -> list[DiagramSummaryRead]:
    """Every saved diagram, sorted by name."""
    return [DiagramSummaryRead.of(diagram) for diagram in await diagrams.list_diagrams()]


@router.post(
    "/diagrams",
    response_model=DiagramSummaryRead,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT,
)
async def create_diagram(body: DiagramCreate, diagrams: Diagrams) -> DiagramSummaryRead:
    """A new, empty diagram. Its name must not be taken."""
    created = await diagrams.create(name=body.name, description=body.description)
    return DiagramSummaryRead.of(created)


@router.get("/diagrams/{diagram_id}", response_model=DiagramRead, responses=NOT_FOUND)
async def read_diagram(diagram_id: UUID, diagrams: Diagrams) -> DiagramRead:
    """A diagram with its boxes, the elements they show and the links between them."""
    return DiagramRead.of(await diagrams.open(diagram_id))


@router.patch("/diagrams/{diagram_id}", response_model=DiagramSummaryRead, responses=REJECTED)
async def update_diagram(
    diagram_id: UUID, body: DiagramUpdate, diagrams: Diagrams
) -> DiagramSummaryRead:
    updated = await diagrams.update(diagram_id, name=body.name, description=body.description)
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
    """Replace every box of a diagram at once.

    Each element must exist and appear once; otherwise nothing is written.
    """
    await diagrams.replace_layout(diagram_id, [node.placed() for node in body.nodes])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
