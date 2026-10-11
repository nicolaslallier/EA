"""The architecture endpoints: the catalogue, the links, and the two traversals.

The traversals are the reason this is a graph and not a table. `/neighbourhood`
answers "show me around this element"; `/impact` answers "what breaks if this
element fails", walking each relationship in the direction its type actually
carries dependency.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import Architecture
from ea.api.schemas import (
    ApprovalRequest,
    AsOf,
    Depth,
    ElementCreate,
    ElementPage,
    ElementRead,
    ElementUpdate,
    ErrorResponse,
    GraphRead,
    Limit,
    Offset,
    RelationshipCreate,
    RelationshipRead,
    Search,
)
from ea.domain.archimate import ElementType, Layer, RelationshipType
from ea.domain.ports import ElementFilter
from ea.domain.query import ElementQuery

router = APIRouter(tags=["architecture"])

# The bounds are `api/schemas.py`'s, shared with the MCP tools; `Query()` only
# says where FastAPI reads the value from.
DepthQuery = Annotated[Depth, Query()]
LimitQuery = Annotated[Limit, Query()]
OffsetQuery = Annotated[Offset, Query()]
AsOfQuery = Annotated[AsOf | None, Query()]

NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}


# --- Elements -------------------------------------------------------------


@router.post(
    "/elements",
    response_model=ElementRead,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def create_element(payload: ElementCreate, service: Architecture) -> ElementRead:
    """Add an element to the catalogue."""
    element = await service.create_element(
        element_type=payload.element_type,
        name=payload.name,
        description=payload.description,
        documentation=payload.documentation,
        properties=payload.properties,
    )
    return ElementRead.of(element)


@router.get("/elements", response_model=ElementPage)
async def list_elements(
    service: Architecture,
    element_type: Annotated[list[ElementType] | None, Query()] = None,
    layer: Annotated[list[Layer] | None, Query()] = None,
    search: Annotated[Search | None, Query()] = None,
    package: Annotated[UUID | None, Query()] = None,
    include_subpackages: Annotated[bool, Query()] = True,
    root_only: Annotated[bool, Query()] = False,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
    as_of: AsOfQuery = None,
) -> ElementPage:
    """Browse the catalogue, narrowed by type, by layer, by a name fragment
    or filed in a package (subpackages included unless told otherwise)."""
    # The same object, the same normalisation and the same refusals as
    # `POST /elements/search`: one filter, whichever door it came through.
    query = ElementQuery(
        element_types=tuple(element_type or ()),
        layers=tuple(layer or ()),
        text=search,
        package_id=package,
        include_subpackages=include_subpackages,
        root_only=root_only,
        limit=limit,
        offset=offset,
    )
    criteria = replace(ElementFilter.from_query(query), as_of=as_of)
    elements = await service.list_elements(criteria)
    return ElementPage(
        items=[ElementRead.of(element) for element in elements],
        total=await service.count_elements(criteria),
        limit=limit,
        offset=offset,
    )


@router.get("/elements/{element_id}", response_model=ElementRead, responses=NOT_FOUND)
async def read_element(element_id: UUID, service: Architecture) -> ElementRead:
    return ElementRead.of(await service.get_element(element_id))


@router.patch("/elements/{element_id}", response_model=ElementRead, responses=NOT_FOUND)
async def update_element(
    element_id: UUID, payload: ElementUpdate, service: Architecture
) -> ElementRead:
    """Change what an element says. Its type is fixed once created."""
    element = await service.update_element(
        element_id,
        name=payload.name,
        description=payload.description,
        documentation=payload.documentation,
        properties=payload.properties,
    )
    return ElementRead.of(element)


@router.delete(
    "/elements/{element_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
async def delete_element(element_id: UUID, service: Architecture) -> Response:
    """Remove an element and every relationship attached to it."""
    await service.delete_element(element_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/elements/{element_id}/approval",
    response_model=ElementRead,
    responses={
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def transition_element(
    element_id: UUID, payload: ApprovalRequest, service: Architecture
) -> ElementRead:
    """Move an element through review.

    403 without the role the token carries, 409 if the state forbids it or if
    `expected_revision` is no longer the element's revision.
    """
    element = await service.transition_element(
        element_id, payload.transition, expected_revision=payload.expected_revision
    )
    return ElementRead.of(element)


# --- Relationships --------------------------------------------------------


@router.post(
    "/relationships",
    response_model=RelationshipRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
async def create_relationship(
    payload: RelationshipCreate, service: Architecture
) -> RelationshipRead:
    """Link two elements, if ArchiMate allows that link between their types."""
    relationship = await service.connect(
        relationship_type=payload.relationship_type,
        source_id=payload.source_id,
        target_id=payload.target_id,
        name=payload.name,
        access_type=payload.access_type,
        directed=payload.directed,
        properties=payload.properties,
    )
    return RelationshipRead.of(relationship)


@router.get("/relationships", response_model=list[RelationshipRead])
async def list_relationships(
    service: Architecture,
    element_id: Annotated[UUID | None, Query()] = None,
    relationship_type: Annotated[list[RelationshipType] | None, Query()] = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
    as_of: AsOfQuery = None,
) -> list[RelationshipRead]:
    """List links, optionally only those touching one element."""
    relationships = await service.list_relationships(
        element_id=element_id,
        relationship_types=tuple(relationship_type or ()),
        limit=limit,
        offset=offset,
        as_of=as_of,
    )
    return [RelationshipRead.of(relationship) for relationship in relationships]


@router.delete(
    "/relationships/{relationship_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
async def delete_relationship(relationship_id: UUID, service: Architecture) -> Response:
    await service.disconnect(relationship_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/elements/{element_id}/relationships",
    response_model=GraphRead,
    responses=NOT_FOUND,
)
async def read_element_relations(
    element_id: UUID,
    service: Architecture,
    relationship_type: Annotated[list[RelationshipType] | None, Query()] = None,
) -> GraphRead:
    """Every link attached to one element, with the elements at both ends.

    `GET /relationships?element_id=` answers with the links alone, which is
    enough to count them but not to display one: a link stores the *types* of
    its endpoints, never their names. This returns the sub-graph instead, so a
    client renders "Invoice API serves Order to cash" from one response.
    """
    view = await service.relations_of(element_id, relationship_types=tuple(relationship_type or ()))
    return GraphRead.of(view)


# --- Traversals -----------------------------------------------------------


@router.get(
    "/elements/{element_id}/neighbourhood",
    response_model=GraphRead,
    responses=NOT_FOUND,
)
async def read_neighbourhood(
    element_id: UUID,
    service: Architecture,
    depth: DepthQuery = 1,
    relationship_type: Annotated[list[RelationshipType] | None, Query()] = None,
    as_of: AsOfQuery = None,
) -> GraphRead:
    """The sub-graph around an element, following links in either direction."""
    view = await service.neighbourhood(
        element_id, depth=depth, relationship_types=tuple(relationship_type or ()), as_of=as_of
    )
    return GraphRead.of(view)


@router.get("/elements/{element_id}/impact", response_model=GraphRead, responses=NOT_FOUND)
async def read_impact(
    element_id: UUID,
    service: Architecture,
    depth: DepthQuery = 5,
    relationship_type: Annotated[list[RelationshipType] | None, Query()] = None,
    as_of: AsOfQuery = None,
) -> GraphRead:
    """What depends on this element, transitively.

    Each hop is walked in the direction dependency actually runs, which is not
    always the direction the arrow is drawn: a service serves a process, but a
    whole is composed *of* its parts.
    """
    view = await service.impact_of(
        element_id, depth=depth, relationship_types=tuple(relationship_type or ()), as_of=as_of
    )
    return GraphRead.of(view)
