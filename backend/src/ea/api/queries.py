"""Structured search of the catalogue, and the saved queries that keep it.

A query is a tree of data (`ElementQuerySchema`), never a string of SQL: the
repository compiles it, every key and value bound. It is built into an
`ElementQuery` here — where a contradiction is refused with a 422 — and the
service neither knows nor cares which adapter it came from.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from ea.api.dependencies import Architecture
from ea.api.schemas import ElementPage, ElementQuerySchema, ElementRead, ErrorResponse
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
