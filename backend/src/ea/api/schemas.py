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
    permitted_relationships,
)
from ea.domain.documents import Document, DocumentSummary
from ea.domain.ipam import (
    DEFAULT_VRF,
    RESERVED_PROPERTY,
    AddressLocation,
    Assignment,
    Subnet,
    SubnetDetail,
)
from ea.domain.model import Element, Relationship
from ea.domain.ports import GraphView
from ea.domain.search import Passage

#: Shared constraints, so every endpoint bounds a payload the same way.
Name = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=2000)]
Documentation = Annotated[str, Field(max_length=20000)]
Properties = Annotated[
    dict[str, str],
    Field(description="Free-form attributes, e.g. owner or criticality."),
]
#: The addressing constraints, bounded once for both adapters (docs/adr/0020).
#: They stop a runaway argument before it reaches the parser; which strings are
#: actually addresses is `domain/ipam.py`'s decision, never restated here.
Vrf = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description="The routing scope an address is unique in.",
    ),
]
Address = Annotated[str, Field(min_length=2, max_length=64, description="An IPv4 or IPv6 address.")]
Reserved = Annotated[
    str,
    Field(
        max_length=2000,
        description="Addresses set aside, comma-separated: `10.0.1.1, 10.0.1.10-10.0.1.20, "
        "10.0.1.128/25`.",
    ),
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

    @classmethod
    def of(cls, element_type: ElementType) -> ElementTypeRead:
        return cls(
            value=element_type,
            label=element_type.label,
            layer=element_type.layer,
            aspect=element_type.aspect,
        )


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

    @classmethod
    def snapshot(cls) -> MetamodelRead:
        """The whole palette, read off the metamodel rather than listed here.

        A classmethod rather than a route body because two adapters answer this
        question — `GET /metamodel` and the `describe_metamodel` tool — and the
        61 types are exactly the kind of list `CLAUDE.md` refuses to see twice.
        """
        return cls(
            element_types=[ElementTypeRead.of(element_type) for element_type in ElementType],
            relationship_types=[
                RelationshipTypeRead.of(relationship) for relationship in RelationshipType
            ],
            layers=list(Layer),
        )


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

    @classmethod
    def for_source(cls, source: ElementType) -> RelationshipMatrixRead:
        """The row of the matrix for one source type, computed from the rules."""
        return cls(
            source=source,
            rules=[
                RelationshipRuleRead(
                    target=target,
                    relationships=list(permitted_relationships(source, target)),
                )
                for target in ElementType
            ],
        )


class DocumentSummaryRead(BaseModel):
    """One attached markdown file, as a listing shows it — without its content.

    The content is deliberately absent: a listing exists to name the files, and
    a client that received ten bodies to draw ten names would download
    megabytes to render a list. `GET /documents/{id}` is the read that carries
    the text.
    """

    id: UUID
    element_id: UUID
    filename: str
    byte_size: int = Field(description="Size of the stored markdown, in bytes.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, summary: DocumentSummary) -> DocumentSummaryRead:
        return cls(
            id=summary.id,
            element_id=summary.element_id,
            filename=summary.filename,
            byte_size=summary.byte_size,
            created_at=summary.created_at,
            updated_at=summary.updated_at,
        )


class DocumentRead(BaseModel):
    """One attached markdown file, content included — the whole document.

    Not a subclass of the summary above: it would inherit an `of` that builds
    it from a `DocumentSummary`, which has no content to give it.
    """

    id: UUID
    element_id: UUID
    filename: str
    byte_size: int = Field(description="Size of the stored markdown, in bytes.")
    created_at: datetime
    updated_at: datetime
    content: str = Field(description="The markdown itself, as text.")

    @classmethod
    def of(cls, document: Document) -> DocumentRead:
        return cls(
            id=document.id,
            element_id=document.element_id,
            filename=document.filename,
            byte_size=document.byte_size,
            created_at=document.created_at,
            updated_at=document.updated_at,
            content=document.content,
        )


class PassageRead(BaseModel):
    """One hit of a document search: a passage, and how close it came.

    A passage and not a file, which is the whole point of docs/adr/0019: an
    answer naming `runbook.md` leaves the reader to find the paragraph, and a
    caller that had to read a megabyte to check one sentence has not been
    helped.

    The element is named by id. Names live in the graph, and decorating a
    relational query with a call to Neo4j would make every search pay for a
    field the caller may not want — `get_element` is one call away when it
    does.

    It lives here, with the API's other read models, though no route serves it
    yet: it is the shape a passage has, and keeping it here is what stops the
    MCP adapter from growing a private rendering of the same thing.
    """

    document_id: UUID
    element_id: UUID
    filename: str
    trail: str = Field(
        description="Where the passage sits, e.g. `runbook.md > Incidents > Escalation`."
    )
    heading_path: list[str] = Field(description="The headings above the passage, outermost first.")
    text: str = Field(description="The passage itself, as markdown.")
    score: float = Field(description="Cosine similarity to the question; larger is closer.")

    @classmethod
    def of(cls, passage: Passage) -> PassageRead:
        return cls(
            document_id=passage.document_id,
            element_id=passage.element_id,
            filename=passage.filename,
            trail=passage.trail,
            heading_path=list(passage.heading_path),
            text=passage.text,
            score=passage.score,
        )


class ErrorResponse(BaseModel):
    """The single error envelope every failing endpoint returns."""

    error: str = Field(description="Stable machine-readable code.")
    detail: str = Field(description="Human-readable message, safe to display.")


# --- IP address management (docs/adr/0020) --------------------------------
# An address is an attribute of an element, so these read models name the
# element rather than wrapping one: a client drawing an inventory wants the
# host's name beside its address, and `ElementRead` in every row would be the
# same catalogue served three times.


class SubnetCreate(_Input):
    """A new subnet: a `communication_network` element carrying a prefix."""

    name: Name
    cidr: Annotated[
        str,
        Field(
            min_length=2,
            max_length=64,
            description="The prefix itself, e.g. `10.0.1.0/24` or `2001:db8::/64`.",
        ),
    ]
    vrf: Vrf = DEFAULT_VRF
    reserved: Reserved = ""
    description: Description = ""


class AddressAssign(_Input):
    """One address, given to one element."""

    element_id: UUID
    address: Address
    vrf: Vrf = DEFAULT_VRF


class AddressAllocate(_Input):
    """Whichever address the subnet has free next, given to one element."""

    element_id: UUID


class SubnetRead(BaseModel):
    """A subnet and how full it is.

    `capacity` is what the prefix can hand out at all, `reserved` what is set
    aside by hand, `used` what is assigned and `free` what is left — four
    numbers rather than a percentage, because a client that only has the
    percentage cannot say "three addresses left" and that is the sentence
    somebody acts on.
    """

    element_id: UUID
    name: str
    description: str
    cidr: str
    vrf: str
    version: int = Field(description="4 or 6.")
    capacity: int
    reserved: int
    used: int
    free: int
    reservations: str = Field(
        description="The reservations exactly as written, so a client can edit them back."
    )

    @classmethod
    def of(cls, subnet: Subnet) -> SubnetRead:
        return cls(
            element_id=subnet.element.id,
            name=subnet.element.name,
            description=subnet.element.description,
            cidr=subnet.network.prefix.with_prefixlen,
            vrf=subnet.network.vrf,
            version=subnet.network.prefix.version,
            capacity=subnet.network.capacity,
            reserved=subnet.network.reserved_count,
            used=subnet.used,
            free=subnet.free,
            reservations=subnet.element.properties.get(RESERVED_PROPERTY, ""),
        )


class AddressRead(BaseModel):
    """One assigned address, with the element answering on it."""

    address: str
    vrf: str
    version: int
    element_id: UUID
    element_name: str
    element_type: ElementType
    subnet_id: UUID | None = Field(
        description="The subnet element this address falls in, if one is declared."
    )

    @classmethod
    def of(cls, assignment: Assignment) -> AddressRead:
        return cls(
            address=str(assignment.address),
            vrf=assignment.vrf,
            version=assignment.address.version,
            element_id=assignment.element.id,
            element_name=assignment.element.name,
            element_type=assignment.element.element_type,
            subnet_id=assignment.subnet_id,
        )


class SubnetDetailRead(BaseModel):
    """A subnet with its occupants and the address it would hand out next."""

    subnet: SubnetRead
    addresses: list[AddressRead]
    next_free: str | None = Field(description="Absent when the subnet is full.")

    @classmethod
    def of(cls, detail: SubnetDetail) -> SubnetDetailRead:
        return cls(
            subnet=SubnetRead.of(detail.subnet),
            addresses=[AddressRead.of(assignment) for assignment in detail.addresses],
            next_free=str(detail.next_free) if detail.next_free is not None else None,
        )


class AddressLocationRead(BaseModel):
    """The answer to "this address, that is what?" — in one payload.

    The assignment says which element answers; the graph says what that element
    is wired to, so the sentence "10.0.1.12 is srv-app-01, and Billing runs on
    it" can be written without a second call.
    """

    address: AddressRead
    graph: GraphRead

    @classmethod
    def of(cls, location: AddressLocation) -> AddressLocationRead:
        return cls(
            address=AddressRead.of(location.assignment),
            graph=GraphRead.of(location.graph),
        )
