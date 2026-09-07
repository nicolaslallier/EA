"""Request and response models for the architecture endpoints.

Requests forbid unknown fields, so a typo in a client is a 422 and not a
silently ignored attribute. Responses are built explicitly from the domain
entities rather than serialised from them, so nothing internal can leak into a
payload by being added to a dataclass.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ea.domain.archimate import (
    AccessType,
    Aspect,
    ElementType,
    Layer,
    RelationshipCategory,
    RelationshipType,
)
from ea.domain.model import Element, Relationship
from ea.domain.ports import GraphView

#: Shared constraints, so every endpoint bounds a payload the same way.
Name = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=2000)]
Documentation = Annotated[str, Field(max_length=20000)]
Properties = Annotated[
    dict[str, str],
    Field(description="Free-form attributes, e.g. owner or criticality."),
]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ElementCreate(_Input):
    """A new architecture element."""

    element_type: ElementType
    name: Name
    description: Description = ""
    documentation: Documentation = ""
    properties: Properties = Field(default_factory=dict)


class ElementUpdate(_Input):
    """A partial update. An omitted field keeps its stored value.

    `element_type` is absent on purpose: changing it could invalidate
    relationships that already exist, which is a migration and not an edit.
    """

    name: Name | None = None
    description: Description | None = None
    documentation: Documentation | None = None
    properties: Properties | None = None


class ElementRead(BaseModel):
    """An element as the API exposes it, layer and aspect resolved for the caller."""

    id: UUID
    element_type: ElementType
    layer: Layer
    aspect: Aspect
    name: str
    description: str
    documentation: str
    properties: dict[str, str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, element: Element) -> ElementRead:
        return cls(
            id=element.id,
            element_type=element.element_type,
            layer=element.element_type.layer,
            aspect=element.element_type.aspect,
            name=element.name,
            description=element.description,
            documentation=element.documentation,
            properties=dict(element.properties),
            created_at=element.created_at,
            updated_at=element.updated_at,
        )


class ElementPage(BaseModel):
    """One page of the catalogue, with the total so a client can paginate."""

    items: list[ElementRead]
    total: int
    limit: int
    offset: int


class RelationshipCreate(_Input):
    """A new link between two existing elements."""

    relationship_type: RelationshipType
    source_id: UUID
    target_id: UUID
    name: Annotated[str, Field(max_length=200)] = ""
    access_type: AccessType | None = Field(
        default=None, description="Only meaningful on an access relationship."
    )
    directed: bool = Field(
        default=False, description="Only meaningful on an association relationship."
    )
    properties: Properties = Field(default_factory=dict)


class RelationshipRead(BaseModel):
    id: UUID
    relationship_type: RelationshipType
    source_id: UUID
    target_id: UUID
    source_type: ElementType
    target_type: ElementType
    name: str
    access_type: AccessType | None
    directed: bool
    properties: dict[str, str]
    created_at: datetime

    @classmethod
    def of(cls, relationship: Relationship) -> RelationshipRead:
        return cls(
            id=relationship.id,
            relationship_type=relationship.relationship_type,
            source_id=relationship.source_id,
            target_id=relationship.target_id,
            source_type=relationship.source_type,
            target_type=relationship.target_type,
            name=relationship.name,
            access_type=relationship.access_type,
            directed=relationship.directed,
            properties=dict(relationship.properties),
            created_at=relationship.created_at,
        )


class GraphRead(BaseModel):
    """A sub-graph: the nodes and the edges between them, ready to be drawn."""

    elements: list[ElementRead]
    relationships: list[RelationshipRead]

    @classmethod
    def of(cls, view: GraphView) -> GraphRead:
        return cls(
            elements=[ElementRead.of(element) for element in view.elements],
            relationships=[
                RelationshipRead.of(relationship) for relationship in view.relationships
            ],
        )


class ElementTypeRead(BaseModel):
    """One entry of the metamodel catalogue, for building a palette in the SPA."""

    value: ElementType
    label: str
    layer: Layer
    aspect: Aspect


class RelationshipTypeRead(BaseModel):
    """One relationship type, with what the metamodel says about using it.

    `label` stays out on purpose: the display form is the client's business —
    the SPA writes these as French verbs — while the family, the strength and
    the direction of dependency are facts of ArchiMate the backend owns.
    """

    value: RelationshipType
    category: RelationshipCategory
    strength: int = Field(description="Higher binds tighter; a derived chain keeps the weakest.")
    impact_follows_direction: bool = Field(
        description="Whether an outage at the source propagates along the arrow."
    )

    @classmethod
    def of(cls, relationship: RelationshipType) -> RelationshipTypeRead:
        return cls(
            value=relationship,
            category=relationship.category,
            strength=relationship.strength,
            impact_follows_direction=relationship.impact_follows_direction,
        )


class MetamodelRead(BaseModel):
    """Everything a client needs to render and validate the ArchiMate palette."""

    element_types: list[ElementTypeRead]
    relationship_types: list[RelationshipTypeRead]
    layers: list[Layer]


class RelationshipRuleRead(BaseModel):
    """Which relationships one source type may open toward one target type."""

    target: ElementType
    relationships: list[RelationshipType]


class RelationshipMatrixRead(BaseModel):
    """One row of the 61x61 matrix of Appendix B, derived from the rules.

    A row rather than the whole matrix: 3721 cells is a payload nobody reads,
    and a client always draws one source at a time.
    """

    source: ElementType
    rules: list[RelationshipRuleRead]


class ErrorResponse(BaseModel):
    """The single error envelope every failing endpoint returns."""

    error: str = Field(description="Stable machine-readable code.")
    detail: str = Field(description="Human-readable message, safe to display.")
