"""Structured search of the catalogue, and the saved queries that keep it.

A query is a tree of data (`ElementQuerySchema`), never a string of SQL: the
repository compiles it, every key and value bound. It is built into an
`ElementQuery` here — where a contradiction is refused with a 422 — and the
service neither knows nor cares which adapter it came from.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import Architecture, SavedQueries
from ea.api.schemas import (
    ElementPage,
    ElementQuerySchema,
    ElementRead,
    ErrorResponse,
    Limit,
    Offset,
    SavedQueryCreate,
    SavedQueryRead,
    SavedQuerySummaryRead,
    SavedQueryUpdate,
)
from ea.domain.ports import ElementFilter

router = APIRouter(tags=["search"])

_ERROR = {"model": ErrorResponse}
INVALID: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR
}


@router.post("/elements/search", response_model=ElementPage, responses=INVALID)
async def search_elements(body: ElementQuerySchema, service: Architecture) -> ElementPage:
    """Types, layers, name fragment, package, attributes and links present or absent.

    Every criterion is combined with AND. `total` counts everything the query
    matches, not the page. Reading needs no more than a login — it is a read.
    """
    query = body.to_domain()
    criteria = ElementFilter.from_query(query)
    elements = await service.list_elements(criteria)
    return ElementPage(
        items=[ElementRead.of(element) for element in elements],
        total=await service.count_elements(criteria),
        limit=query.limit,
        offset=query.offset,
    )


LimitQuery = Annotated[Limit, Query()]
OffsetQuery = Annotated[Offset, Query()]
NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_404_NOT_FOUND: _ERROR}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}


@router.get("/queries", response_model=list[SavedQuerySummaryRead])
async def list_saved_queries(queries: SavedQueries) -> list[SavedQuerySummaryRead]:
    """The shared, named filters. The queries themselves are not read for a list."""
    return [SavedQuerySummaryRead.of(summary) for summary in await queries.list_queries()]


@router.post(
    "/queries",
    response_model=SavedQueryRead,
    status_code=status.HTTP_201_CREATED,
    responses=REJECTED,
)
async def create_saved_query(body: SavedQueryCreate, queries: SavedQueries) -> SavedQueryRead:
    """Keep a filter under a name, for the whole team. The author is the caller."""
    saved = await queries.create(
        name=body.name, description=body.description, query=body.query.to_domain()
    )
    return SavedQueryRead.read(saved)


@router.get("/queries/{query_id}", response_model=SavedQueryRead, responses=REJECTED)
async def read_saved_query(query_id: UUID, queries: SavedQueries) -> SavedQueryRead:
    return SavedQueryRead.read(await queries.get(query_id))


@router.patch("/queries/{query_id}", response_model=SavedQueryRead, responses=REJECTED)
async def update_saved_query(
    query_id: UUID, body: SavedQueryUpdate, queries: SavedQueries
) -> SavedQueryRead:
    saved = await queries.update(
        query_id,
        name=body.name,
        description=body.description,
        query=body.query.to_domain() if body.query is not None else None,
    )
    return SavedQueryRead.read(saved)


@router.delete("/queries/{query_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
async def delete_saved_query(query_id: UUID, queries: SavedQueries) -> Response:
    """Delete the query — not the elements it finds."""
    await queries.delete(query_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/queries/{query_id}/elements", response_model=ElementPage, responses=REJECTED)
async def run_saved_query(
    query_id: UUID, queries: SavedQueries, limit: LimitQuery = 50, offset: OffsetQuery = 0
) -> ElementPage:
    """Run a saved query, paged. 422 `unknown_package` if its package has since been deleted."""
    result = await queries.run(query_id, limit=limit, offset=offset)
    return ElementPage(
        items=[ElementRead.of(element) for element in result.elements],
        total=result.total,
        limit=limit,
        offset=offset,
    )
