"""Request and response models for the architecture endpoints.

Requests forbid unknown fields, so a typo in a client is a 422 and not a
silently ignored attribute. Responses are built explicitly from the domain
entities rather than serialised from them, so nothing internal can leak into a
payload by being added to a dataclass.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from itertools import islice
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ea.domain.analytics import DIMENSION_PATTERN, MAX_BUCKETS, Aggregate, Metric
from ea.domain.approval import ApprovalState, Transition, allowed_transitions
from ea.domain.archimate import (
    AccessType,
    Aspect,
    ElementType,
    Layer,
    RelationshipCategory,
    RelationshipType,
    permitted_relationships,
)
from ea.domain.audit import (
    AuditAction,
    AuditChannel,
    AuditEvent,
    AuditPage,
    EntityKind,
    FieldChange,
)
from ea.domain.baselines import (
    MAX_BASELINE_DESCRIPTION_LENGTH,
    MAX_BASELINE_NAME_LENGTH,
    Baseline,
    BaselineDiff,
    EntityRef,
    Modified,
)
from ea.domain.diagram_objects import MAX_OBJECT_TEXT, MAX_WAYPOINTS, ObjectKind
from ea.domain.diagram_objects import DiagramEdge as PlacedEdge
from ea.domain.diagram_objects import DiagramObject as PlacedObject
from ea.domain.diagram_objects import Point as PlacedPoint
from ea.domain.diagrams import Diagram, DiagramDetail
from ea.domain.diagrams import DiagramNode as PlacedNode
from ea.domain.documents import Document, DocumentSummary
from ea.domain.exchange import (
    Category,
    ExchangeFormat,
    ImportReport,
    Issue,
    Severity,
)
from ea.domain.files import (
    MAX_FILE_DESCRIPTION_LENGTH,
    MAX_FILE_TAGS,
    MAX_FILE_TITLE_LENGTH,
    MAX_KEY_BYTES,
    MAX_LISTED_ENTRIES,
    MAX_TAG_LENGTH,
    CatalogedFile,
    CatalogedListing,
    FileDetails,
    FileMetadata,
)
from ea.domain.ipam import (
    DEFAULT_VRF,
    RESERVED_PROPERTY,
    AddressLocation,
    Assignment,
    Subnet,
    SubnetDetail,
)
from ea.domain.matrix import MAX_MATRIX_AXIS, AxisSelection, Matrix, MatrixCell
from ea.domain.model import Element, Relationship
from ea.domain.model import StereotypeRef as StereotypeRefModel
from ea.domain.packages import MAX_MOVE_BATCH, MAX_PACKAGE_NAME_LENGTH, PackageSummary
from ea.domain.ports import GraphView
from ea.domain.profiles import (
    MAX_NAME_LENGTH,
    MAX_PROPERTIES_PER_STEREOTYPE,
    MAX_STEREOTYPES_PER_PROFILE,
    MAX_TEMPLATE_LENGTH,
    IconKey,
    Profile,
    PropertyKind,
    Stereotype,
    StereotypeProperty,
)
from ea.domain.quality import MAX_FINDINGS, Finding, QualityPage, RuleCode
from ea.domain.query import (
    MAX_PROPERTY_TESTS,
    MAX_PROPERTY_VALUE,
    MAX_RELATION_TESTS,
    QUERY_VERSION,
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
)
from ea.domain.saved_queries import (
    MAX_SAVED_QUERY_DESCRIPTION,
    MAX_SAVED_QUERY_NAME_LENGTH,
    SavedQuery,
    SavedQuerySummary,
)
from ea.domain.search import Passage
from ea.domain.timeline import PlateauComparison, PlateauSummary, Roadmap
from ea.repositories.architecture_store import MAX_TRAVERSAL_DEPTH
from ea.services.baselines import RestoreOutcome
from ea.services.caller import current_caller

#: Shared constraints, so every endpoint bounds a payload the same way — and so
#: does every MCP tool, which imports these rather than restating them. The two
#: adapters describe a field in their own words (a model and a developer need
#: different sentences) by layering a `Field(description=...)` over the type;
#: the bounds themselves are declared here and nowhere else.
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
Cidr = Annotated[
    str,
    Field(
        min_length=2,
        max_length=64,
        description="The prefix itself, e.g. `10.0.1.0/24` or `2001:db8::/64`.",
    ),
]

#: The bounds of a page, a traversal, a name search and a link's label. Plain
#: `Field`s, because `mcp/` takes them as they are; `api/` wraps each one in
#: `Query()`, which keeps the bounds of the type it is laid over.
Limit = Annotated[int, Field(ge=1, le=200)]
Offset = Annotated[int, Field(ge=0)]
Depth = Annotated[int, Field(ge=1, le=MAX_TRAVERSAL_DEPTH)]
Search = Annotated[str, Field(max_length=200)]
#: A day to read the model on (docs/adr/0060): what is outside its `valid_from` /
#: `valid_until` that day is left out. A typed `date`, never free text.
AsOf = Annotated[
    date,
    Field(
        description=(
            "Read the model as it is on this day (YYYY-MM-DD): what is not in service "
            "that day is left out. Leave it out for no date filter."
        )
    ),
]
LinkName = Annotated[str, Field(max_length=200)]
#: The audit filters, bounded once for both adapters (docs/adr/0046). A NUL is
#: refused here because PostgreSQL cannot compare a `text` column to one: it
#: would reach the driver and come back as a 500.
Actor = Annotated[str, Field(max_length=200, pattern=r"^[^\x00]*$")]
RequestIdFilter = Annotated[str, Field(max_length=128, pattern=r"^[^\x00]*$")]
CursorToken = Annotated[str, Field(max_length=512)]
#: The baselines (docs/adr/0047). The lengths are the domain's, imported, so the
#: schema and `Baseline.create` cannot drift into refusing different values.
BaselineName = Annotated[str, Field(min_length=1, max_length=MAX_BASELINE_NAME_LENGTH)]
BaselineDescription = Annotated[str, Field(max_length=MAX_BASELINE_DESCRIPTION_LENGTH)]
Against = Annotated[
    str,
    Field(max_length=36, description="A baseline id, or `current` for the model as it is now."),
]
#: What a dry run of a restoration hands out (`plan_digest`, a SHA-256) and what
#: applying must hand back.
PlanDigest = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
#: The revision of an element an approver read (docs/adr/0048): a positive counter.
ExpectedRevision = Annotated[
    int,
    Field(
        ge=1,
        le=2**62,
        description="The `revision` of the element as you read it: required to approve.",
    ),
]

# --- Files in MinIO (docs/adr/0036), and what is known about them (docs/adr/0039) ---
FileKey = Annotated[str, Field(min_length=1, max_length=MAX_KEY_BYTES)]
FilePrefix = Annotated[str, Field(max_length=MAX_KEY_BYTES)]
FileTitle = Annotated[str, Field(max_length=MAX_FILE_TITLE_LENGTH)]
FileDescription = Annotated[str, Field(max_length=MAX_FILE_DESCRIPTION_LENGTH)]
FileTag = Annotated[str, Field(max_length=MAX_TAG_LENGTH)]
FileTags = Annotated[list[FileTag], Field(max_length=MAX_FILE_TAGS)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ElementCreate(_Input):
    """A new architecture element."""

    element_type: ElementType
    name: Name
    description: Description = ""
    documentation: Documentation = ""
    properties: Properties = Field(default_factory=dict)


class ApprovalRequest(_Input):
    """One step of review. `approve` must say which revision the approver read."""

    transition: Transition
    expected_revision: ExpectedRevision | None = None

    @model_validator(mode="after")
    def _approving_names_a_revision(self) -> ApprovalRequest:
        if self.transition is Transition.APPROVE and self.expected_revision is None:
            msg = "approving needs expected_revision, the revision of the element you read"
            raise ValueError(msg)
        return self


class ElementUpdate(_Input):
    """A partial update. An omitted field keeps its stored value.

    `element_type` is absent on purpose: changing it could invalidate
    relationships that already exist, which is a migration and not an edit.
    """

    name: Name | None = None
    description: Description | None = None
    documentation: Documentation | None = None
    properties: Properties | None = None


#: `#rrggbb` only: a colour ends up in an SVG attribute, so anything else is an injection.
Colour = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class StereotypeRef(BaseModel):
    """The stereotype an element carries - enough to draw it, none of its rules."""

    id: UUID
    name: str
    colour: Colour | None
    icon_key: IconKey

    @classmethod
    def of(cls, ref: StereotypeRefModel) -> StereotypeRef:
        return cls(id=ref.id, name=ref.name, colour=ref.colour, icon_key=IconKey(ref.icon_key))


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
    stereotypes: list[StereotypeRef]
    package_id: UUID | None
    created_at: datetime
    updated_at: datetime
    approval_state: ApprovalState
    locked: bool
    approved_by: str | None = Field(description="The Keycloak subject that approved it.")
    approved_at: datetime | None
    revision: int = Field(description="Counts the changes to its content; an approval names one.")
    allowed_transitions: list[Transition] = Field(
        description="What the caller may do next with this element: the SPA draws a button each."
    )

    @classmethod
    def of(cls, element: Element) -> ElementRead:
        # Outside a request nobody is calling, and nothing is offered.
        caller = current_caller.get()
        offered = (
            allowed_transitions(element.approval_state, element.locked, caller)
            if caller is not None
            else ()
        )
        return cls(
            id=element.id,
            element_type=element.element_type,
            layer=element.element_type.layer,
            aspect=element.element_type.aspect,
            name=element.name,
            description=element.description,
            documentation=element.documentation,
            properties=dict(element.properties),
            stereotypes=[StereotypeRef.of(ref) for ref in element.stereotypes],
            package_id=element.package_id,
            created_at=element.created_at,
            updated_at=element.updated_at,
            approval_state=element.approval_state,
            locked=element.locked,
            approved_by=element.approved_by_sub,
            approved_at=element.approved_at,
            revision=element.revision,
            allowed_transitions=list(offered),
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
    name: LinkName = ""
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


class FileDetailsWrite(_Input):
    """What a person writes about a file — replaced whole, never merged.

    Every field has a default, so a body naming only `tags` clears the title:
    the whole description is what is sent, which is the only shape a client can
    implement without reading the current values first.
    """

    title: FileTitle = ""
    description: FileDescription = ""
    tags: FileTags = Field(default_factory=list)

    def details(self) -> FileDetails:
        return FileDetails(title=self.title, description=self.description, tags=tuple(self.tags))


class FileMetadataRead(BaseModel):
    """What PostgreSQL knows about one file of the bucket — see docs/adr/0039.

    `etag` is deliberately absent: it is how a reconcile tells a replaced object
    from an untouched one, and means nothing to a client. `sha256` is here
    because "the same file under another name" is a question a person asks; it
    is `null` for a file this API never received, which is an honest unknown
    rather than a digest nobody computed.
    """

    title: str
    description: str
    tags: list[str]
    uploaded_by: str = Field(
        description="The username of whoever uploaded it, empty for a file "
        "written straight into the bucket."
    )
    sha256: str | None = Field(
        description="Hex digest of the file's bytes, null when this API never received them."
    )
    created_at: datetime = Field(description="When this catalogue first saw the file.")
    updated_at: datetime

    @classmethod
    def of(cls, metadata: FileMetadata) -> FileMetadataRead:
        return cls(
            title=metadata.details.title,
            description=metadata.details.description,
            tags=list(metadata.details.tags),
            uploaded_by=metadata.uploaded_by,
            sha256=metadata.sha256,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
        )


class FileRead(BaseModel):
    """One file of the bucket, as a listing or an upload describes it — never its bytes.

    `metadata` is `null` for a file the bucket holds and the catalogue has not
    heard of — one the pipeline or the MinIO console wrote. That is a file with
    no description, never a missing file: the bytes are what exist.
    """

    key: str = Field(description="The full path in the bucket, folders included.")
    name: str = Field(description="The last segment of the key.")
    size: int = Field(description="Size of the stored file, in bytes.")
    last_modified: datetime
    content_type: str
    metadata: FileMetadataRead | None = None

    @classmethod
    def of(cls, cataloged: CatalogedFile) -> FileRead:
        stored = cataloged.stored
        return cls(
            key=stored.key,
            name=stored.name,
            size=stored.size,
            last_modified=stored.last_modified,
            content_type=stored.content_type,
            metadata=(
                None if cataloged.metadata is None else FileMetadataRead.of(cataloged.metadata)
            ),
        )


class FileListingRead(BaseModel):
    """One folder: its sub-folders, then its files."""

    prefix: str
    folders: list[str] = Field(description="Each sub-folder as a full prefix ending in `/`.")
    files: list[FileRead]
    truncated: bool = Field(
        description=f"True when only the first {MAX_LISTED_ENTRIES} entries are listed."
    )

    @classmethod
    def of(cls, listing: CatalogedListing) -> FileListingRead:
        return cls(
            prefix=listing.prefix,
            folders=list(listing.folders),
            files=[FileRead.of(found) for found in listing.files],
            truncated=listing.truncated,
        )


class ReconcileRead(BaseModel):
    """What one pass of the file catch-up changed — see docs/adr/0039."""

    recorded: int = Field(description="Files the catalogue had never heard of.")
    forgotten: int = Field(description="Rows whose file is no longer in the bucket.")


class PassageRead(BaseModel):
    """One hit of a document search: a passage, and how close it came.

    A passage and not a file, which is the whole point of docs/adr/0019: an
    answer naming `runbook.md` leaves the reader to find the paragraph, and a
    caller that had to read a megabyte to check one sentence has not been
    helped.

    The element is named by id. Names live in the graph, and decorating a
    passage query with a read of the graph would make every search pay for a
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


class MeRead(BaseModel):
    """The caller, as far as the SPA needs to know it."""

    username: str
    can_write: bool
    can_approve: bool


# --- IP address management (docs/adr/0020) --------------------------------
# An address is an attribute of an element, so these read models name the
# element rather than wrapping one: a client drawing an inventory wants the
# host's name beside its address, and `ElementRead` in every row would be the
# same catalogue served three times.


class SubnetCreate(_Input):
    """A new subnet: a `communication_network` element carrying a prefix."""

    name: Name
    cidr: Cidr
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


# --- Saved diagrams (docs/adr/0031) ---------------------------------------
# A diagram is a view: its read model carries the elements and relationships
# it shows as the catalogue's own `ElementRead` and `RelationshipRead`, so the
# SPA draws a box from exactly what the catalogue would show.

#: A diagram a person can still read. Past this it is an export, not a view.
MAX_DIAGRAM_NODES: Final = 500
#: How far from the origin a box may sit, in canvas units, either way.
MAX_COORDINATE: Final = 100_000
Coordinate = Annotated[float, Field(ge=-MAX_COORDINATE, le=MAX_COORDINATE, allow_inf_nan=False)]
#: How small and how large a box may be drawn, in canvas units, either side.
MIN_BOX_SIZE: Final = 20
MAX_BOX_SIZE: Final = 5_000
BoxSize = Annotated[float, Field(ge=MIN_BOX_SIZE, le=MAX_BOX_SIZE, allow_inf_nan=False)]
#: Notes and groups on one diagram, and the links whose route was changed.
MAX_DIAGRAM_OBJECTS: Final = 500
MAX_DIAGRAM_EDGES: Final = 2_000
#: Stacking order, either way. Only the order matters, not the number.
ZIndex = Annotated[int, Field(ge=-10_000, le=10_000)]
Revision = Annotated[int, Field(ge=0)]
ObjectText = Annotated[str, Field(max_length=MAX_OBJECT_TEXT)]


class DiagramCreate(_Input):
    name: Name
    description: Description = ""
    package_id: UUID | None = None


class DiagramUpdate(_Input):
    """A partial update. An omitted field keeps its stored value.

    `package_id` is three-valued like a package's `parent_id`: absent keeps the
    package, `null` puts the diagram at the root.
    """

    name: Name | None = None
    description: Description | None = None
    package_id: UUID | None = None


class DiagramNode(_Input):
    """One box: an element, the top-left corner of the box and its size, in canvas units."""

    element_id: UUID
    x: Coordinate
    y: Coordinate
    # Required, not defaulted: a default would split this model into an input
    # and an output schema, and rename the type the SPA imports.
    width: BoxSize
    height: BoxSize
    parent_object_id: UUID | None
    z: ZIndex

    @classmethod
    def of(cls, node: PlacedNode) -> DiagramNode:
        return cls(
            element_id=node.element_id,
            x=node.x,
            y=node.y,
            width=node.width,
            height=node.height,
            parent_object_id=node.parent_object_id,
            z=node.z,
        )

    def placed(self) -> PlacedNode:
        return PlacedNode(
            element_id=self.element_id,
            x=self.x,
            y=self.y,
            width=self.width,
            height=self.height,
            parent_object_id=self.parent_object_id,
            z=self.z,
        )


class DiagramPoint(_Input):
    x: Coordinate
    y: Coordinate


class DiagramEdge(_Input):
    """The route of one link on this diagram. A link with no row is drawn straight."""

    relationship_id: UUID
    waypoints: Annotated[list[DiagramPoint], Field(max_length=MAX_WAYPOINTS)]

    @classmethod
    def of(cls, edge: PlacedEdge) -> DiagramEdge:
        return cls(
            relationship_id=edge.relationship_id,
            waypoints=[DiagramPoint(x=p.x, y=p.y) for p in edge.waypoints],
        )

    def placed(self) -> PlacedEdge:
        return PlacedEdge(
            relationship_id=self.relationship_id,
            waypoints=tuple(PlacedPoint(x=p.x, y=p.y) for p in self.waypoints),
        )


class DiagramObject(_Input):
    """A note (its text is the body) or a group (its text is the title). Never a fact."""

    id: UUID
    kind: ObjectKind
    x: Coordinate
    y: Coordinate
    width: BoxSize
    height: BoxSize
    text: ObjectText
    colour: Colour | None
    z: ZIndex
    parent_id: UUID | None
    anchor_element_id: UUID | None

    @classmethod
    def of(cls, item: PlacedObject) -> DiagramObject:
        return cls(
            id=item.id,
            kind=item.kind,
            x=item.x,
            y=item.y,
            width=item.width,
            height=item.height,
            text=item.text,
            colour=item.colour,
            z=item.z,
            parent_id=item.parent_id,
            anchor_element_id=item.anchor_element_id,
        )

    def placed(self) -> PlacedObject:
        return PlacedObject(
            id=self.id,
            kind=self.kind,
            x=self.x,
            y=self.y,
            width=self.width,
            height=self.height,
            text=self.text,
            colour=self.colour,
            z=self.z,
            parent_id=self.parent_id,
            anchor_element_id=self.anchor_element_id,
        )


class DiagramLayout(_Input):
    """The whole scene of a diagram, replacing the stored one — against the revision the client saw.

    Every field is required, empty lists included: an optional one would split
    this model into `-Input` and `-Output` schemas (docs/adr/0035).
    """

    revision: Revision
    nodes: Annotated[list[DiagramNode], Field(max_length=MAX_DIAGRAM_NODES)]
    objects: Annotated[list[DiagramObject], Field(max_length=MAX_DIAGRAM_OBJECTS)]
    edges: Annotated[list[DiagramEdge], Field(max_length=MAX_DIAGRAM_EDGES)]


class DiagramSummaryRead(BaseModel):
    id: UUID
    name: str
    description: str
    node_count: int
    package_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, diagram: Diagram) -> DiagramSummaryRead:
        return cls(
            id=diagram.id,
            name=diagram.name,
            description=diagram.description,
            node_count=diagram.node_count,
            package_id=diagram.package_id,
            created_at=diagram.created_at,
            updated_at=diagram.updated_at,
        )


class DiagramRead(BaseModel):
    """A diagram opened: its boxes, the elements they show, the links between them."""

    id: UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    package_id: UUID | None
    revision: int = Field(
        description="Send it back with the next PUT /layout: a stale one is a 409."
    )
    nodes: list[DiagramNode]
    objects: list[DiagramObject]
    edges: list[DiagramEdge]
    elements: list[ElementRead]
    relationships: list[RelationshipRead] = Field(
        description="Only the relationships whose two ends are both on the diagram."
    )
    inactive_element_ids: list[UUID] = Field(
        default_factory=list,
        description="Boxes whose element is not in service on the requested `as_of` day.",
    )
    inactive_relationship_ids: list[UUID] = Field(
        default_factory=list,
        description="Links out of period on `as_of`, or whose either end is.",
    )

    @classmethod
    def of(cls, detail: DiagramDetail) -> DiagramRead:
        diagram = detail.diagram
        return cls(
            id=diagram.id,
            name=diagram.name,
            description=diagram.description,
            created_at=diagram.created_at,
            updated_at=diagram.updated_at,
            package_id=diagram.package_id,
            revision=diagram.revision,
            nodes=[DiagramNode.of(node) for node in detail.nodes],
            objects=[DiagramObject.of(item) for item in detail.objects],
            edges=[DiagramEdge.of(edge) for edge in detail.edges],
            elements=[ElementRead.of(element) for element in detail.graph.elements],
            relationships=[RelationshipRead.of(link) for link in detail.graph.relationships],
            inactive_element_ids=sorted(detail.inactive_elements, key=str),
            inactive_relationship_ids=sorted(detail.inactive_relationships, key=str),
        )


# --- Packages: the classification tree ---------------------------------------
# A package files elements and diagrams; it is not an ArchiMate element and
# has no relationship. The bounds come from `domain/packages.py`, which the
# services need too; they are re-declared as Pydantic types here, once, for
# both adapters.

PackageName = Annotated[
    str, Field(min_length=1, max_length=MAX_PACKAGE_NAME_LENGTH, pattern=r"^[^/]+$")
]
#: What an agent or a query string says where a body would say `null`.
ROOT_PACKAGE: Final = "root"
PackageTarget = Annotated[
    str,
    Field(
        min_length=1,
        max_length=36,
        description="The id of a package, or `root` for the top of the tree.",
    ),
]


def package_target(value: str) -> UUID | None:
    """`root` is the top of the tree (`None`); anything else must be a package id.

    One parser for both adapters: `DELETE /packages/{id}?move_contents_to=` and
    the MCP tools cannot express "the root" as `null`, so they say `root`.
    """
    if value == ROOT_PACKAGE:
        return None
    try:
        return UUID(value)
    except ValueError:
        msg = f"expected a package id or {ROOT_PACKAGE!r}, got {value!r}"
        raise ValueError(msg) from None


class PackageCreate(_Input):
    name: PackageName
    description: Description = ""
    parent_id: UUID | None = None


class PackageUpdate(_Input):
    """A partial update.

    `parent_id` is three-valued: absent keeps the parent, `null` puts the
    package at the root, an id moves it there — which is why the route reads
    `model_fields_set` rather than the value.
    """

    name: PackageName | None = None
    description: Description | None = None
    parent_id: UUID | None = None


class PackageRead(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str
    description: str
    path: str = Field(description="Names from the root down, e.g. `Finance / Budget`.")
    element_count: int = Field(description="Elements filed directly in this package.")
    diagram_count: int = Field(description="Diagrams filed directly in this package.")
    child_count: int = Field(description="Packages directly below this one.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, summary: PackageSummary) -> PackageRead:
        package = summary.package
        return cls(
            id=package.id,
            parent_id=package.parent_id,
            name=package.name,
            description=package.description,
            path=summary.path,
            element_count=summary.counts.elements,
            diagram_count=summary.counts.diagrams,
            child_count=summary.counts.children,
            created_at=package.created_at,
            updated_at=package.updated_at,
        )


class MoveElements(_Input):
    """File up to `MAX_MOVE_BATCH` elements in a package, or at the root."""

    element_ids: Annotated[list[UUID], Field(min_length=1, max_length=MAX_MOVE_BATCH)]
    package_id: UUID | None = Field(description="The package, or `null` for the root.")


# --- Structured search ------------------------------------------------------
# `ElementQuerySchema` is the strict translation of `domain.query.ElementQuery`,
# and its dump is the form a saved query is stored in. Every field is required
# and none has a default, on purpose: it is both a request body and (in a saved
# query) a response, and a default would split the component into an input and
# an output type in the OpenAPI document and rename what the SPA imports — the
# reason `DiagramNode` is built the same way. The SPA and the MCP tools always
# build the whole object.

PropertyKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z][A-Za-z0-9_]{0,62}$",
        description="The name of a user-defined attribute: letters, digits, underscores.",
    ),
]
PropertyValue = Annotated[str, Field(max_length=MAX_PROPERTY_VALUE)]


class PropertyTestSchema(_Input):
    """One test on a user-defined attribute.

    `value` is required by `eq` and `contains`, refused by the others.
    """

    key: PropertyKey
    op: PropertyOp
    value: PropertyValue | None

    def to_domain(self) -> PropertyTest:
        return PropertyTest(key=self.key, op=self.op, value=self.value)


class RelationTestSchema(_Input):
    """Whether an element has (or has no) relationship of one type, optionally to one type."""

    relationship_type: RelationshipType
    direction: Direction
    present: bool = Field(description="`false` finds the elements that have no such link.")
    other_type: ElementType | None = Field(description="The type at the far end, if it matters.")

    def to_domain(self) -> RelationTest:
        return RelationTest(
            relationship_type=self.relationship_type,
            direction=self.direction,
            present=self.present,
            other_type=self.other_type,
        )


class ElementQuerySchema(_Input):
    """A structured search of the catalogue; every criterion is combined with AND."""

    version: Literal[1]
    element_types: Annotated[list[ElementType], Field(max_length=len(ElementType))]
    layers: Annotated[list[Layer], Field(max_length=len(Layer))]
    text: Search | None
    package_id: UUID | None
    include_subpackages: bool
    root_only: bool
    properties: Annotated[list[PropertyTestSchema], Field(max_length=MAX_PROPERTY_TESTS)]
    relations: Annotated[list[RelationTestSchema], Field(max_length=MAX_RELATION_TESTS)]
    order: QueryOrder
    limit: Limit
    offset: Offset

    def to_domain(self) -> ElementQuery:
        """The validated domain object — where `root_only` + `package_id` is refused."""
        return ElementQuery(
            element_types=tuple(self.element_types),
            layers=tuple(self.layers),
            text=self.text,
            package_id=self.package_id,
            include_subpackages=self.include_subpackages,
            root_only=self.root_only,
            properties=tuple(test.to_domain() for test in self.properties),
            relations=tuple(test.to_domain() for test in self.relations),
            order=self.order,
            limit=self.limit,
            offset=self.offset,
        )

    @classmethod
    def of(cls, query: ElementQuery) -> ElementQuerySchema:
        return cls(
            version=QUERY_VERSION,
            element_types=list(query.element_types),
            layers=list(query.layers),
            text=query.text,
            package_id=query.package_id,
            include_subpackages=query.include_subpackages,
            root_only=query.root_only,
            properties=[
                PropertyTestSchema(key=test.key, op=test.op, value=test.value)
                for test in query.properties
            ],
            relations=[
                RelationTestSchema(
                    relationship_type=test.relationship_type,
                    direction=test.direction,
                    present=test.present,
                    other_type=test.other_type,
                )
                for test in query.relations
            ],
            order=query.order,
            limit=query.limit,
            offset=query.offset,
        )


# --- Saved queries -------------------------------------------------------------
# Shared by every reader; `created_by` is read from the caller, so the body has
# no such field (and `extra="forbid"` turns an attempt to send one into a 422).
# The bounds are the domain's, imported rather than restated, like the packages'.

SavedQueryName = Annotated[str, Field(min_length=1, max_length=MAX_SAVED_QUERY_NAME_LENGTH)]
SavedQueryDescription = Annotated[str, Field(max_length=MAX_SAVED_QUERY_DESCRIPTION)]


class SavedQueryCreate(_Input):
    name: SavedQueryName
    description: SavedQueryDescription = ""
    query: ElementQuerySchema


class SavedQueryUpdate(_Input):
    """A partial update. An omitted field keeps its stored value."""

    name: SavedQueryName | None = None
    description: SavedQueryDescription | None = None
    query: ElementQuerySchema | None = None


class SavedQuerySummaryRead(BaseModel):
    id: UUID
    name: str
    description: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, summary: SavedQuerySummary) -> SavedQuerySummaryRead:
        return cls(
            id=summary.id,
            name=summary.name,
            description=summary.description,
            created_by=summary.created_by,
            created_at=summary.created_at,
            updated_at=summary.updated_at,
        )


class SavedQueryRead(SavedQuerySummaryRead):
    query: ElementQuerySchema

    @classmethod
    def read(cls, saved: SavedQuery) -> SavedQueryRead:
        return cls(
            **SavedQuerySummaryRead.of(saved.summary).model_dump(),
            query=ElementQuerySchema.of(saved.query),
        )


# --- The audit trail (docs/adr/0046) ----------------------------------------


class FieldChangeRead(BaseModel):
    field: str
    before: Any = None
    after: Any = None


class AuditEventRead(BaseModel):
    """One use case that changed the catalogue — the diff is computed server-side,
    so a client displays it and never decides what counts as a change."""

    id: UUID
    at: datetime
    actor_sub: str
    actor_name: str
    action: AuditAction
    entity_kind: EntityKind
    entity_id: UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str
    channel: AuditChannel
    correlation: str | None
    changes: list[FieldChangeRead]

    @classmethod
    def of(cls, event: AuditEvent) -> AuditEventRead:
        return cls(
            id=event.id,
            at=event.at,
            actor_sub=event.actor_sub,
            actor_name=event.actor_name,
            action=event.action,
            entity_kind=event.entity_kind,
            entity_id=event.entity_id,
            before=dict(event.before) if event.before is not None else None,
            after=dict(event.after) if event.after is not None else None,
            request_id=event.request_id,
            channel=event.channel,
            correlation=event.correlation,
            changes=[_change(change) for change in event.changes],
        )


def _change(change: FieldChange) -> FieldChangeRead:
    return FieldChangeRead(field=change.field, before=change.before, after=change.after)


class AuditPageRead(BaseModel):
    items: list[AuditEventRead]
    next_cursor: str | None = Field(
        description="Pass it back as `cursor` for the next page; absent on the last one."
    )

    @classmethod
    def of(cls, page: AuditPage) -> AuditPageRead:
        return cls(
            items=[AuditEventRead.of(event) for event in page.events],
            next_cursor=page.next_cursor,
        )


# --- Baselines (docs/adr/0047) ------------------------------------------------

#: How many entities of each kind a restoration plan names. The counts are exact
#: and unbounded; the names are the first few, because a baseline can hold twenty
#: thousand elements and an answer of that size is of no use to anyone.
PLAN_LISTED_ITEMS: Final = 20
#: A warning quotes names from the model; it is cut so one cannot be a megabyte.
PLAN_WARNING_LENGTH: Final = 300


class BaselineCreate(_Input):
    name: BaselineName
    description: BaselineDescription = ""


class BaselineRead(BaseModel):
    id: UUID
    name: str
    description: str
    created_by: str
    created_at: datetime
    element_count: int
    relationship_count: int
    box_count: int

    @classmethod
    def of(cls, baseline: Baseline) -> BaselineRead:
        return cls(
            id=baseline.id,
            name=baseline.name,
            description=baseline.description,
            created_by=baseline.created_by,
            created_at=baseline.created_at,
            element_count=baseline.element_count,
            relationship_count=baseline.relationship_count,
            box_count=baseline.box_count,
        )


class EntityRefRead(BaseModel):
    kind: str
    id: UUID
    label: str
    type: str

    @classmethod
    def of(cls, ref: EntityRef) -> EntityRefRead:
        return cls(kind=ref.kind, id=ref.id, label=ref.label, type=ref.type)


class ModifiedRead(EntityRefRead):
    changes: list[FieldChangeRead]

    @classmethod
    def of_modified(cls, modified: Modified) -> ModifiedRead:
        return cls(
            kind=modified.kind,
            id=modified.id,
            label=modified.label,
            type=modified.type,
            changes=[_change(change) for change in modified.changes],
        )


class BaselineDiffRead(BaseModel):
    elements_added: list[EntityRefRead]
    elements_removed: list[EntityRefRead]
    elements_modified: list[ModifiedRead]
    relationships_added: list[EntityRefRead]
    relationships_removed: list[EntityRefRead]
    relationships_modified: list[ModifiedRead]

    @classmethod
    def of(cls, diff: BaselineDiff) -> BaselineDiffRead:
        return cls(
            elements_added=[EntityRefRead.of(r) for r in diff.elements_added],
            elements_removed=[EntityRefRead.of(r) for r in diff.elements_removed],
            elements_modified=[ModifiedRead.of_modified(m) for m in diff.elements_modified],
            relationships_added=[EntityRefRead.of(r) for r in diff.relationships_added],
            relationships_removed=[EntityRefRead.of(r) for r in diff.relationships_removed],
            relationships_modified=[
                ModifiedRead.of_modified(m) for m in diff.relationships_modified
            ],
        )


class RestoreRequest(_Input):
    dry_run: bool = Field(default=True, description="Report what would change; write nothing.")
    expected_digest: PlanDigest | None = Field(
        default=None,
        description="The `digest` of the dry run you read. Required to apply (dry_run false): "
        "a model that moved since is refused with `restore_conflict`.",
    )
    keep_new: bool | None = Field(
        default=None, description="Keep what was created since; false (exact) when omitted."
    )


class RestorePlanRead(BaseModel):
    """What a restoration would do. Every figure is an exact count; the lists name
    only the first few of each kind (`PLAN_LISTED_ITEMS`)."""

    elements_to_create: int
    elements_to_update: int
    elements_to_delete: int
    relationships_to_create: int
    relationships_to_delete: int
    relationships_cascaded: int
    boxes_to_restore: int
    documents_lost: int
    boxes_lost: int
    created: list[EntityRefRead]
    updated: list[EntityRefRead]
    deleted: list[EntityRefRead]
    warning_count: int
    warnings: list[str]
    blocked_count: int
    blocked: list[EntityRefRead]


class RestoreRead(BaseModel):
    applied: bool
    digest: str = Field(description="Send it back as `expected_digest` to apply this very plan.")
    plan: RestorePlanRead

    @classmethod
    def of(cls, outcome: RestoreOutcome) -> RestoreRead:
        plan = outcome.plan

        def named(elements: Sequence[Element]) -> list[EntityRefRead]:
            return [
                EntityRefRead(kind="element", id=e.id, label=e.name, type=e.element_type.value)
                for e in islice(elements, PLAN_LISTED_ITEMS)
            ]

        return cls(
            applied=outcome.applied,
            digest=outcome.digest,
            plan=RestorePlanRead(
                elements_to_create=len(plan.elements_to_create),
                elements_to_update=len(plan.elements_to_update),
                elements_to_delete=len(plan.elements_to_delete),
                relationships_to_create=len(plan.relationships_to_create),
                relationships_to_delete=len(plan.relationships_to_delete),
                relationships_cascaded=plan.cascaded_relationships,
                boxes_to_restore=len(plan.boxes_to_restore),
                documents_lost=plan.documents_lost,
                boxes_lost=plan.boxes_lost,
                created=named(plan.elements_to_create),
                updated=named([u.current for u in plan.elements_to_update]),
                deleted=named(plan.elements_to_delete),
                warning_count=len(plan.warnings),
                warnings=[w[:PLAN_WARNING_LENGTH] for w in plan.warnings[:PLAN_LISTED_ITEMS]],
                blocked_count=len(plan.blocked),
                blocked=named(plan.blocked),
            ),
        )


class IssueRead(BaseModel):
    """One line of an import report. Texts are cut and cleaned: they never carry the file."""

    severity: Severity
    code: str
    message: str
    source_id: str | None
    line: int | None
    path: str | None

    @classmethod
    def of(cls, issue: Issue) -> IssueRead:
        return cls(
            severity=issue.severity,
            code=issue.code,
            message=issue.message,
            source_id=issue.source_id,
            line=issue.line,
            path=issue.path,
        )


class CountsRead(BaseModel):
    created: int
    reused: int
    rejected: int
    ignored: int


class ImportCountsRead(BaseModel):
    elements: CountsRead
    relationships: CountsRead
    packages: CountsRead
    views: CountsRead


class ImportReportRead(BaseModel):
    """What an import did, or would do: counts that are always exact, lines that may be cut."""

    dry_run: bool
    applied: bool = Field(description="True only if the transaction was committed.")
    format: ExchangeFormat
    sha256: str = Field(description="Digest of the received file: a confirmation re-sends it.")
    counts: ImportCountsRead
    issues: list[IssueRead]
    truncated: bool = Field(description="True when `issues` was cut at the report ceiling.")
    issue_count: int = Field(description="Every line the import produced, kept or not.")
    duration_ms: int = Field(description="How long the import took, in milliseconds.")

    @classmethod
    def of(cls, report: ImportReport) -> ImportReportRead:
        def counts(category: Category) -> CountsRead:
            found = report.counts[category]
            return CountsRead(
                created=found.created,
                reused=found.reused,
                rejected=found.rejected,
                ignored=found.ignored,
            )

        return cls(
            dry_run=report.dry_run,
            applied=report.applied,
            format=report.format,
            sha256=report.sha256,
            counts=ImportCountsRead(
                elements=counts(Category.ELEMENTS),
                relationships=counts(Category.RELATIONSHIPS),
                packages=counts(Category.PACKAGES),
                views=counts(Category.VIEWS),
            ),
            issues=[IssueRead.of(issue) for issue in report.issues],
            truncated=report.truncated,
            issue_count=report.issue_count,
            duration_ms=report.duration_ms,
        )


# --- Model analysis (docs/adr/0056) ---------------------------------

#: How many ids one axis may *submit*. The service refuses more than
#: `MAX_MATRIX_AXIS` with its own typed error; this only stops a runaway body.
MAX_SUBMITTED_AXIS_IDS: Final = 10 * MAX_MATRIX_AXIS


class AxisSelect(_Input):
    """What one axis of the matrix is made of. Nothing set means the whole catalogue."""

    element_ids: list[UUID] = Field(
        default_factory=list,
        max_length=MAX_SUBMITTED_AXIS_IDS,
        description=f"Named elements; more than {MAX_MATRIX_AXIS} is refused.",
    )
    element_types: list[ElementType] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    search: Search | None = None
    package_id: UUID | None = Field(
        default=None, description="A package, sub-packages included. Not with `query_id`."
    )
    query_id: UUID | None = Field(
        default=None, description="A saved query, evaluated now. Not with `package_id`."
    )

    def selection(self) -> AxisSelection:
        return AxisSelection(
            element_ids=tuple(self.element_ids),
            element_types=tuple(self.element_types),
            layers=tuple(self.layers),
            search=self.search,
            package_id=self.package_id,
            query_id=self.query_id,
        )


class MatrixQuery(_Input):
    """Both axes. A POST only because they travel in a body: nothing is written."""

    rows: AxisSelect
    cols: AxisSelect


class ElementBrief(BaseModel):
    """An element abridged to what a header needs."""

    id: UUID
    name: str
    element_type: ElementType
    layer: Layer

    @classmethod
    def of(cls, element: Element) -> ElementBrief:
        return cls(
            id=element.id,
            name=element.name,
            element_type=element.element_type,
            layer=element.element_type.layer,
        )


class MatrixCellRead(BaseModel):
    """A cell holding at least one link: row = source, column = target."""

    source_id: UUID
    target_id: UUID
    links: list[RelationshipRead]


class BlockedCellRead(BaseModel):
    source_id: UUID
    target_id: UUID


class MatrixRead(BaseModel):
    """The grid: two axes and the cells that hold links."""

    rows: list[ElementBrief]
    cols: list[ElementBrief]
    rows_total: int
    cols_total: int
    truncated: bool
    cells: list[MatrixCellRead]
    blocked: list[BlockedCellRead]

    @classmethod
    def of(cls, matrix: Matrix) -> MatrixRead:
        return cls(
            rows=[ElementBrief.of(element) for element in matrix.rows.elements],
            cols=[ElementBrief.of(element) for element in matrix.cols.elements],
            rows_total=matrix.rows.total,
            cols_total=matrix.cols.total,
            truncated=matrix.rows.truncated or matrix.cols.truncated,
            cells=[
                MatrixCellRead(
                    source_id=cell.source_id,
                    target_id=cell.target_id,
                    links=[RelationshipRead.of(link) for link in cell.links],
                )
                for cell in matrix.cells
            ],
            blocked=[
                BlockedCellRead(source_id=source, target_id=target)
                for source, target in matrix.blocked
            ],
        )


class MatrixCellDetailRead(BaseModel):
    """One ordered pair: its links, and the relationships ArchiMate would still allow."""

    source_id: UUID
    target_id: UUID
    links: list[RelationshipRead]
    allowed: list[RelationshipType]

    @classmethod
    def of(cls, cell: MatrixCell) -> MatrixCellDetailRead:
        return cls(
            source_id=cell.source_id,
            target_id=cell.target_id,
            links=[RelationshipRead.of(link) for link in cell.links],
            allowed=list(cell.allowed),
        )


# --- Quality checks (docs/adr/0057) ---------------------------------

FindingsLimit = Annotated[int, Field(ge=1, le=MAX_FINDINGS)]
Cursor = Annotated[int, Field(ge=0, description="Where the previous page stopped.")]


def fix_hint_of(finding: Finding) -> str:
    """An application link to where the finding is fixed — a route, never code."""
    if finding.rule is RuleCode.INCONSISTENT_RELATIONSHIP:
        return f"/analyse/voisinage?element={finding.element_id}"
    return f"/elements?element={finding.element_id}"


class FindingRead(BaseModel):
    rule: RuleCode
    severity: Severity
    element_id: UUID
    element_name: str
    related_id: UUID | None
    related_name: str | None
    message: str
    fix_hint: str

    @classmethod
    def of(cls, finding: Finding) -> FindingRead:
        return cls(
            rule=finding.rule,
            severity=finding.severity,
            element_id=finding.element_id,
            element_name=finding.element_name,
            related_id=finding.related_id,
            related_name=finding.related_name,
            message=finding.message,
            fix_hint=fix_hint_of(finding),
        )


class RuleCountRead(BaseModel):
    rule: RuleCode
    severity: Severity
    count: int


class QualityReportRead(BaseModel):
    """One page of findings, with the counts of the whole run."""

    total: int = Field(description="Findings of the whole run, before any filter.")
    counts: list[RuleCountRead]
    findings: list[FindingRead]
    next_cursor: int | None
    partial: bool = Field(
        description="True when a rule stopped at its budget: a short list proves little."
    )

    @classmethod
    def of(cls, page: QualityPage) -> QualityReportRead:
        return cls(
            total=len(page.report.findings),
            counts=[
                RuleCountRead(rule=c.rule, severity=c.severity, count=c.count)
                for c in page.report.counts
            ],
            findings=[FindingRead.of(finding) for finding in page.findings],
            next_cursor=page.next_cursor,
            partial=page.report.partial,
        )


# --- Aggregates and heat maps (docs/adr/0058) -------------------------

DimensionParam = Annotated[
    str,
    Field(
        pattern=DIMENSION_PATTERN,
        description="`element_type`, `layer`, or `property:<key>` for an attribute of the element.",
    ),
]


class BucketRead(BaseModel):
    """One group. `key` is a value, or `__unset__` / `__invalid__` for a property dimension."""

    key: str
    count: int
    value: float | None = Field(
        description="The metric over the bucket; null if nothing was readable."
    )
    level: int | None = Field(description="0 (lowest) to 4, decided by the server; null if none.")


class AggregateRead(BaseModel):
    by: str
    metric: Metric
    property_key: str | None
    total: int
    unreadable: int = Field(description="Elements whose property is present but not a number.")
    truncated: bool = Field(description=f"True when more than {MAX_BUCKETS} buckets were cut.")
    buckets: list[BucketRead]

    @classmethod
    def of(cls, aggregate: Aggregate) -> AggregateRead:
        return cls(
            by=str(aggregate.dimensions[0]),
            metric=aggregate.metric,
            property_key=aggregate.property_key,
            total=aggregate.total,
            unreadable=aggregate.unreadable,
            truncated=aggregate.truncated,
            buckets=[
                BucketRead(key=b.keys[0], count=b.count, value=b.value, level=b.level)
                for b in aggregate.buckets
            ],
        )


class HeatCellRead(BaseModel):
    row: str
    col: str
    count: int
    value: float | None
    level: int | None


class HeatmapRead(BaseModel):
    rows_by: str
    cols_by: str
    rows: list[str] = Field(description="Row keys, in the order the dimension reads.")
    cols: list[str]
    metric: Metric
    property_key: str | None
    total: int
    unreadable: int
    truncated: bool
    cells: list[HeatCellRead]

    @classmethod
    def of(cls, aggregate: Aggregate) -> HeatmapRead:
        return cls(
            rows_by=str(aggregate.dimensions[0]),
            cols_by=str(aggregate.dimensions[1]),
            rows=list(aggregate.axis_keys(0)),
            cols=list(aggregate.axis_keys(1)),
            metric=aggregate.metric,
            property_key=aggregate.property_key,
            total=aggregate.total,
            unreadable=aggregate.unreadable,
            truncated=aggregate.truncated,
            cells=[
                HeatCellRead(
                    row=b.keys[0], col=b.keys[1], count=b.count, value=b.value, level=b.level
                )
                for b in aggregate.buckets
            ],
        )


# --- Time: plateaus, the gap between two, the roadmap (docs/adr/0061) -------------


class PlateauRead(BaseModel):
    """A plateau, with when it is reached and what it contains."""

    id: UUID
    name: str
    valid_from: date | None = Field(description="When this state is reached; none: undated.")
    valid_until: date | None
    member_count: int = Field(description="Elements it composes or aggregates, counted once.")

    @classmethod
    def of(cls, summary: PlateauSummary) -> PlateauRead:
        return cls(
            id=summary.element.id,
            name=summary.element.name,
            valid_from=summary.lifespan.start,
            valid_until=summary.lifespan.end,
            member_count=summary.member_count,
        )


class PlateauGapRead(BaseModel):
    """What changes between plateau `a` and plateau `b`, and the gaps already drawn."""

    a: ElementRead
    b: ElementRead
    only_in_a: list[ElementRead] = Field(description="What leaves: in `a`, not in `b`.")
    only_in_b: list[ElementRead] = Field(description="What arrives: in `b`, not in `a`.")
    common: list[ElementRead]
    gaps: list[ElementRead] = Field(description="The gap elements already associated to both.")

    @classmethod
    def of(cls, comparison: PlateauComparison) -> PlateauGapRead:
        return cls(
            a=ElementRead.of(comparison.a),
            b=ElementRead.of(comparison.b),
            only_in_a=[ElementRead.of(e) for e in comparison.diff.only_in_a],
            only_in_b=[ElementRead.of(e) for e in comparison.diff.only_in_b],
            common=[ElementRead.of(e) for e in comparison.diff.common],
            gaps=[ElementRead.of(e) for e in comparison.gaps],
        )


class GapCreate(BaseModel):
    """Draw the gap between two plateaus."""

    model_config = ConfigDict(extra="forbid")

    a: UUID
    b: UUID
    name: Name | None = Field(default=None, description="Default: « Écart <a> → <b> ».")


class RoadmapItemRead(BaseModel):
    id: UUID
    name: str
    element_type: ElementType
    valid_from: date | None
    valid_until: date | None
    dated: bool = Field(description="False: listed apart, in the undated band.")
    plateau_ids: list[UUID] = Field(description="The plateaus of the roadmap that contain it.")


class RoadmapEdgeRead(BaseModel):
    source_id: UUID
    target_id: UUID
    relationship_type: RelationshipType


class RoadmapRead(BaseModel):
    """Plateaus, work packages and deliverables with their dates, and the links between them."""

    items: list[RoadmapItemRead]
    edges: list[RoadmapEdgeRead]
    window_from: date | None = Field(description="Asked for, else the first date of the data.")
    window_to: date | None

    @classmethod
    def of(cls, roadmap: Roadmap) -> RoadmapRead:
        return cls(
            items=[
                RoadmapItemRead(
                    id=item.element.id,
                    name=item.element.name,
                    element_type=item.element.element_type,
                    valid_from=item.lifespan.start,
                    valid_until=item.lifespan.end,
                    dated=item.lifespan.is_dated,
                    plateau_ids=list(item.plateau_ids),
                )
                for item in roadmap.items
            ],
            edges=[
                RoadmapEdgeRead(
                    source_id=e.source_id,
                    target_id=e.target_id,
                    relationship_type=e.relationship_type,
                )
                for e in roadmap.edges
            ],
            window_from=roadmap.window_from,
            window_to=roadmap.window_to,
        )


# --- Profiles and stereotypes (docs/adr/0064) --------------

#: The size of a profile document an import accepts.
MAX_PROFILE_DOCUMENT_BYTES: Final = 256 * 1024
_ProfileName = Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH)]


class StereotypePropertySchema(_Input):
    """One declared property. Every field is required: no default, no `-Input` twin."""

    key: Annotated[str, Field(min_length=1, max_length=63)]
    label: Annotated[str, Field(min_length=1, max_length=100)]
    required: bool
    kind: PropertyKind
    allowed_values: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=500)]], Field(max_length=50)
    ]
    template: Annotated[str, Field(max_length=MAX_TEMPLATE_LENGTH)] | None
    help: Annotated[str, Field(max_length=300)]

    @classmethod
    def of(cls, prop: StereotypeProperty) -> StereotypePropertySchema:
        return cls(
            key=prop.key,
            label=prop.label,
            required=prop.required,
            kind=prop.kind,
            allowed_values=list(prop.allowed_values),
            template=prop.template,
            help=prop.help,
        )

    def declared(self) -> StereotypeProperty:
        return StereotypeProperty(
            key=self.key,
            label=self.label,
            required=self.required,
            kind=self.kind,
            allowed_values=tuple(self.allowed_values),
            template=self.template,
            help=self.help,
        )


class StereotypeWrite(_Input):
    name: _ProfileName
    base_type: ElementType
    colour: Colour | None
    icon_key: IconKey
    properties: Annotated[
        list[StereotypePropertySchema], Field(max_length=MAX_PROPERTIES_PER_STEREOTYPE)
    ]


class StereotypeRead(BaseModel):
    id: UUID
    profile_id: UUID
    name: str
    base_type: ElementType
    colour: str | None
    icon_key: IconKey
    properties: list[StereotypePropertySchema]

    @classmethod
    def of(cls, stereotype: Stereotype) -> StereotypeRead:
        return cls(
            id=stereotype.id,
            profile_id=stereotype.profile_id,
            name=stereotype.name,
            base_type=stereotype.base_type,
            colour=stereotype.colour,
            icon_key=stereotype.icon_key,
            properties=[StereotypePropertySchema.of(p) for p in stereotype.properties],
        )


class ProfileCreate(_Input):
    name: _ProfileName
    description: Description = ""


class ProfileUpdate(_Input):
    name: _ProfileName | None = None
    description: Description | None = None


class ProfileRead(BaseModel):
    id: UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    stereotypes: list[StereotypeRead]

    @classmethod
    def of(cls, profile: Profile) -> ProfileRead:
        return cls(
            id=profile.id,
            name=profile.name,
            description=profile.description,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            stereotypes=[StereotypeRead.of(s) for s in profile.stereotypes],
        )


class ProfileDocument(_Input):
    """A profile as a portable document: no ids, no dates, a version."""

    profile_format: Literal[1]
    name: _ProfileName
    description: Description
    stereotypes: Annotated[list[StereotypeWrite], Field(max_length=MAX_STEREOTYPES_PER_PROFILE)]


class ElementStereotypesSet(_Input):
    """The whole set of stereotypes an element carries: it replaces the previous one."""

    stereotype_ids: Annotated[list[UUID], Field(max_length=20)]
