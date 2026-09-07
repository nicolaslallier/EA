"""The metamodel, exposed so a client can build a palette and pre-validate.

The SPA must never re-declare the 61 element types or reimplement the rules:
it asks here, and the answer comes from the same code the API validates with.
The MCP adapter asks the same question through `describe_metamodel`, which is
why the assembly lives on the schemas rather than in these route bodies.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ea.api.schemas import (
    MetamodelRead,
    RelationshipMatrixRead,
)
from ea.domain.archimate import (
    ElementType,
    RelationshipType,
    permitted_relationships,
)

router = APIRouter(prefix="/metamodel", tags=["metamodel"])


@router.get("", response_model=MetamodelRead)
async def read_metamodel() -> MetamodelRead:
    """Every element type, relationship type and layer ArchiMate 3.2 defines."""
    return MetamodelRead.snapshot()


@router.get("/relationships", response_model=list[RelationshipType])
async def read_permitted_relationships(
    source: Annotated[ElementType, Query(description="Type of the element the link starts at.")],
    target: Annotated[ElementType, Query(description="Type of the element the link ends at.")],
) -> list[RelationshipType]:
    """Which relationships may run between two element types, strongest first."""
    return list(permitted_relationships(source, target))


@router.get("/matrix", response_model=RelationshipMatrixRead)
async def read_relationship_matrix(
    source: Annotated[ElementType, Query(description="Type the links start at.")],
) -> RelationshipMatrixRead:
    """One row of the metamodel matrix: what `source` may point at, and how.

    Appendix B publishes the whole 61x61 grid; a row is what a screen shows at
    a time, and asking for one keeps this a lookup instead of a 3721-cell
    payload. The cells are computed here, never stored, so they cannot drift
    from the rules the API rejects a link with.
    """
    return RelationshipMatrixRead.for_source(source)
