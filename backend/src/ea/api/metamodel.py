"""The metamodel, exposed so a client can build a palette and pre-validate.

The SPA must never re-declare the 61 element types or reimplement the rules:
it asks here, and the answer comes from the same code the API validates with.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ea.api.schemas import (
    ElementTypeRead,
    MetamodelRead,
    RelationshipMatrixRead,
    RelationshipRuleRead,
    RelationshipTypeRead,
)
from ea.domain.archimate import (
    ElementType,
    Layer,
    RelationshipType,
    permitted_relationships,
)

router = APIRouter(prefix="/metamodel", tags=["metamodel"])


@router.get("", response_model=MetamodelRead)
async def read_metamodel() -> MetamodelRead:
    """Every element type, relationship type and layer ArchiMate 3.2 defines."""
    return MetamodelRead(
        element_types=[
            ElementTypeRead(
                value=element_type,
                label=element_type.label,
                layer=element_type.layer,
                aspect=element_type.aspect,
            )
            for element_type in ElementType
        ],
        relationship_types=[
            RelationshipTypeRead.of(relationship) for relationship in RelationshipType
        ],
        layers=list(Layer),
    )


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
    return RelationshipMatrixRead(
        source=source,
        rules=[
            RelationshipRuleRead(
                target=target,
                relationships=list(permitted_relationships(source, target)),
            )
            for target in ElementType
        ],
    )
