"""The tools an agent may call, and what each one is allowed to do.

One tool per use case the services already expose — the same list
`api/architecture.py`, `api/metamodel.py` and `api/documents.py` serve over
HTTP, in the same order, so the two adapters can be read side by side. Nothing
here decides anything: a tool binds its arguments, calls a service, and renders
the answer with the very models the REST API renders, so an agent and the SPA
are told the same thing about the same element.

Two services, because the catalogue spans two stores: the graph in Neo4j and
the markdown attached to its elements in PostgreSQL (docs/adr/0017). They are
kept apart here exactly as they are in `api/`, since the rule that binds them —
an element must exist before a file hangs off it — is `DocumentService`'s, and
an adapter holding a repository instead would be free to skip it.

Both are fetched through callables rather than held, because the application
builds them during its lifespan and this module is assembled before that: see
`main.create_app`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Final
from uuid import UUID

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ea.api.schemas import (
    AddressLocationRead,
    AddressRead,
    DocumentRead,
    DocumentSummaryRead,
    ElementPage,
    ElementRead,
    GraphRead,
    MetamodelRead,
    PassageRead,
    RelationshipMatrixRead,
    RelationshipRead,
    SubnetDetailRead,
    SubnetRead,
)
from ea.domain.archimate import (
    AccessType,
    ElementType,
    Layer,
    RelationshipType,
)
from ea.domain.archimate import (
    # Aliased because the tool below carries the same name: inside
    # `build_mcp_server` the bare name would resolve to the tool itself.
    permitted_relationships as permitted_between,
)
from ea.domain.documents import MAX_DOCUMENT_BYTES, MAX_FILENAME_LENGTH
from ea.domain.ipam import DEFAULT_VRF
from ea.domain.ports import ElementFilter
from ea.domain.search import DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT
from ea.mcp.errors import speaking_plainly
from ea.repositories.archimate_graph import MAX_TRAVERSAL_DEPTH
from ea.services.architecture import ArchitectureService
from ea.services.documents import DocumentService
from ea.services.ipam import IpamService

#: Where the transport is served. The SPA's base URL and this share a host, so
#: it is a path and not a port — see `docs/adr/0014`.
MCP_PATH: Final = "/mcp"

#: Told to the client once, at initialisation. It is the only place an agent
#: learns the two rules that would otherwise cost it a failed call each: that
#: the palette is asked for rather than guessed, and that a type is fixed.
INSTRUCTIONS: Final = """\
This server is the catalogue of an enterprise architecture, modelled in
ArchiMate 3.2 and stored as a graph.

Before creating anything, call `describe_metamodel`: element types are a closed
list of 61 names and relationship types a closed list of 11, and a name that is
not on those lists is refused. Before linking two elements, call
`permitted_relationships` with the two *types*: ArchiMate allows only certain
relationships between certain types, and the server enforces that.

An element's type is fixed once it is created; `update_element` cannot change
it, because stored links are legal only for the types they were made between.

`neighbourhood` answers "what is around this element", following links either
way. `impact_of` answers "what breaks if this element fails", following each
link in the direction dependency actually runs — which is not always the
direction the arrow is drawn.

An element may also carry markdown documents — a runbook, an interface
contract, a decision note — each a named file kept whole. `list_documents`
names them and gives their sizes; `read_document` is what carries the text, so
read one document rather than every document to find out what is there.

`search_documents` is the way in when you do not already know which document
holds the answer: it searches the *passages* of every document by meaning
rather than by keyword, and each hit says which section of which file it came
from. Prefer it to reading documents one by one; `read_document` is then for
the one you found.

The catalogue also holds the IP addressing, and it is not a separate register:
a subnet is a `communication_network` element carrying its prefix, and an
address is an attribute of the element that answers on it. So a host is created
with `create_element` like anything else, and `assign_ip_address` gives it one.

Never write an address with `update_element`. Use `allocate_ip_address`, which
picks the first free address of a subnet, or `assign_ip_address` for a
particular one; both refuse an address already taken, one the prefix keeps for
itself, and one no declared subnet holds. An address must sit inside a subnet
somebody declared, so `declare_ip_subnet` comes first.

`locate_ip_address` is the question this is all for: it says which element
answers on an address **and what that element is wired to**, in one call.\
"""

#: The services the tools call, looked up per call. See the module docstring.
ServiceProvider = Callable[[], ArchitectureService]
DocumentProvider = Callable[[], DocumentService]
IpamProvider = Callable[[], IpamService]

# --- Shared argument constraints, bounded exactly as the HTTP adapter is ----
ElementId = Annotated[
    UUID, Field(description="The id of an element, as returned when it was created.")
]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=2000)]
Documentation = Annotated[str, Field(max_length=20000)]
Properties = Annotated[
    dict[str, str] | None,
    Field(description="Free-form attributes, e.g. owner or criticality. Keys are identifiers."),
]
Depth = Annotated[int, Field(ge=1, le=MAX_TRAVERSAL_DEPTH)]
DocumentId = Annotated[
    UUID, Field(description="The id of a document, as `list_documents` reports it.")
]
Filename = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_FILENAME_LENGTH,
        description="The file's own name, ending in .md or .markdown.",
    ),
]
#: The bound is in characters and the real one, in `domain/documents.py`, is in
#: bytes: this only stops a runaway argument before it is built into a request,
#: and the domain still decides. A character is at least one byte, so a string
#: this allows can still be refused below — never the other way round.
Markdown = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_DOCUMENT_BYTES,
        description="The document itself, as markdown text.",
    ),
]
Limit = Annotated[int, Field(ge=1, le=200)]
Question = Annotated[
    str,
    Field(
        min_length=1,
        max_length=1000,
        description="What you want to find, phrased as a question or a topic.",
    ),
]
SearchLimit = Annotated[int, Field(ge=1, le=MAX_SEARCH_LIMIT)]
Offset = Annotated[int, Field(ge=0)]
Vrf = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description="The routing scope. Leave it alone unless the model has more than one.",
    ),
]
IpAddressArgument = Annotated[
    str, Field(min_length=2, max_length=64, description="An IPv4 or IPv6 address.")
]
Cidr = Annotated[
    str,
    Field(
        min_length=2,
        max_length=64,
        description="A prefix, e.g. `10.0.1.0/24` or `2001:db8::/64`.",
    ),
]
ReservedAddresses = Annotated[
    str,
    Field(
        max_length=2000,
        description=(
            "Addresses to keep out of automatic allocation, comma-separated. "
            "Each entry is an address, a `first-last` range, or a prefix: "
            "`10.0.1.1, 10.0.1.200-10.0.1.254`."
        ),
    ),
]

# --- What a tool does to the graph, said in the protocol's own terms --------
# A client shows these to the person behind the agent, who decides from them
# whether to confirm a call. Getting one wrong is how a delete gets waved
# through, so every tool below carries one.
READS: Final = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
ADDS: Final = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
EDITS: Final = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
REMOVES: Final = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)


def build_mcp_server(
    get_service: ServiceProvider,
    get_documents: DocumentProvider,
    get_ipam: IpamProvider,
    *,
    version: str = "0.1.0",
) -> MCPServer[Any]:
    """Assemble the tool set over the architecture and document services.

    Taking providers rather than the services keeps this callable before the
    application has opened its databases, and lets a test hand over the same
    in-memory doubles the API tests use.

    `get_documents` and `get_ipam` are required rather than optional, so that
    an app assembled without a relational store or without a graph fails when
    one of their tools is *called* — with the wiring fault `document_service_of`
    and `ipam_service_of` state — instead of quietly offering an agent a
    shorter tool list than the one this module documents.
    """
    server: MCPServer[Any] = MCPServer(
        "ea-architecture",
        title="Enterprise Architecture",
        instructions=INSTRUCTIONS,
        version=version,
    )

    # --- Elements ---------------------------------------------------------

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def create_element(
        element_type: ElementType,
        name: Name,
        description: Description = "",
        documentation: Documentation = "",
        properties: Properties = None,
    ) -> ElementRead:
        """Add one element to the catalogue.

        `element_type` must be one of the 61 names `describe_metamodel` lists;
        it cannot be changed afterwards. The answer carries the id every other
        tool takes, plus the layer and aspect the type resolves to.
        """
        return ElementRead.of(
            await get_service().create_element(
                element_type=element_type,
                name=name,
                description=description,
                documentation=documentation,
                properties=properties,
            )
        )

    @server.tool(annotations=READS)
    @speaking_plainly
    async def get_element(element_id: ElementId) -> ElementRead:
        """Read one element in full, by id."""
        return ElementRead.of(await get_service().get_element(element_id))

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_elements(
        element_types: list[ElementType] | None = None,
        layers: list[Layer] | None = None,
        search: Annotated[str | None, Field(max_length=200)] = None,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> ElementPage:
        """Browse the catalogue, narrowed by type, by layer or by a name fragment.

        All three criteria are combined with AND. `total` counts everything the
        criteria match, not the page, so a follow-up call can page through it.
        """
        criteria = ElementFilter(
            element_types=tuple(element_types or ()),
            layers=tuple(layers or ()),
            search=search,
            limit=limit,
            offset=offset,
        )
        service = get_service()
        return ElementPage(
            items=[ElementRead.of(element) for element in await service.list_elements(criteria)],
            total=await service.count_elements(criteria),
            limit=limit,
            offset=offset,
        )

    @server.tool(annotations=EDITS)
    @speaking_plainly
    async def update_element(
        element_id: ElementId,
        name: Name | None = None,
        description: Description | None = None,
        documentation: Documentation | None = None,
        properties: Properties = None,
    ) -> ElementRead:
        """Change what an element says. An omitted field keeps its stored value.

        `properties` is replaced wholesale when given, not merged: read the
        element first if you mean to add one attribute. The element's type
        cannot be changed here — that would be a migration, not an edit.
        """
        return ElementRead.of(
            await get_service().update_element(
                element_id,
                name=name,
                description=description,
                documentation=documentation,
                properties=properties,
            )
        )

    @server.tool(annotations=REMOVES)
    @speaking_plainly
    async def delete_element(element_id: ElementId) -> str:
        """Remove an element **and every relationship attached to it**.

        There is no undo and no soft delete: the links disappear with the
        element. Confirm with the person you are working for before calling it.
        """
        await get_service().delete_element(element_id)
        return f"element {element_id} and every relationship attached to it were deleted"

    # --- Relationships ----------------------------------------------------

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def connect_elements(
        relationship_type: RelationshipType,
        source_id: ElementId,
        target_id: ElementId,
        name: Annotated[str, Field(max_length=200)] = "",
        access_type: AccessType | None = None,
        directed: bool = False,
        properties: Properties = None,
    ) -> RelationshipRead:
        """Link two existing elements, if ArchiMate allows that link.

        Call `permitted_relationships` with the two element types first: a pair
        the metamodel forbids is refused here, and so is a composition or
        aggregation that would make containment cyclic. `access_type` means
        something only on an access relationship, `directed` only on an
        association.
        """
        return RelationshipRead.of(
            await get_service().connect(
                relationship_type=relationship_type,
                source_id=source_id,
                target_id=target_id,
                name=name,
                access_type=access_type,
                directed=directed,
                properties=properties,
            )
        )

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_relationships(
        element_id: ElementId | None = None,
        relationship_types: list[RelationshipType] | None = None,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> list[RelationshipRead]:
        """List links, optionally only those touching one element.

        This answers with the links alone, which is enough to count them but
        not to describe one: a link stores the ids and types of its endpoints,
        never their names. Use `read_element_relations` to get the names too.
        """
        return [
            RelationshipRead.of(relationship)
            for relationship in await get_service().list_relationships(
                element_id=element_id,
                relationship_types=tuple(relationship_types or ()),
                limit=limit,
                offset=offset,
            )
        ]

    @server.tool(annotations=READS)
    @speaking_plainly
    async def read_element_relations(
        element_id: ElementId,
        relationship_types: list[RelationshipType] | None = None,
    ) -> GraphRead:
        """Every link attached to one element, with the elements at both ends.

        The sub-graph, so a sentence like "Billing realizes Invoice API" can be
        written from one answer.
        """
        return GraphRead.of(
            await get_service().relations_of(
                element_id, relationship_types=tuple(relationship_types or ())
            )
        )

    @server.tool(annotations=REMOVES)
    @speaking_plainly
    async def disconnect_elements(relationship_id: ElementId) -> str:
        """Remove one link. The elements it joined are left alone."""
        await get_service().disconnect(relationship_id)
        return f"relationship {relationship_id} was deleted"

    # --- Traversals -------------------------------------------------------

    @server.tool(annotations=READS)
    @speaking_plainly
    async def neighbourhood(
        element_id: ElementId,
        depth: Depth = 1,
        relationship_types: list[RelationshipType] | None = None,
    ) -> GraphRead:
        """What is around an element, within `depth` hops, following links either way.

        The question a diagram answers. For "what breaks if this fails", use
        `impact_of` instead — it walks the arrows, this one ignores them.
        """
        return GraphRead.of(
            await get_service().neighbourhood(
                element_id, depth=depth, relationship_types=tuple(relationship_types or ())
            )
        )

    @server.tool(annotations=READS)
    @speaking_plainly
    async def impact_of(
        element_id: ElementId,
        depth: Depth = 5,
        relationship_types: list[RelationshipType] | None = None,
    ) -> GraphRead:
        """What depends on an element, transitively — what breaks if it fails.

        Each hop is walked in the direction dependency actually runs, which is
        not always the direction the arrow is drawn: a service serves a
        process, but a whole is composed *of* its parts. The answer is the
        sub-graph reached; `impact_follows_direction` on each relationship type
        (from `describe_metamodel`) says which way each hop was taken.
        """
        return GraphRead.of(
            await get_service().impact_of(
                element_id, depth=depth, relationship_types=tuple(relationship_types or ())
            )
        )

    # --- Documents --------------------------------------------------------
    # The markdown attached to an element, which lives in PostgreSQL while the
    # element lives in the graph (docs/adr/0017). The HTTP adapter takes a
    # `multipart/form-data` upload here; an agent has no file to upload, so
    # these take the text itself and land on the service's text entry points.

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def attach_document(
        element_id: ElementId, filename: Filename, content: Markdown
    ) -> DocumentRead:
        """Attach a markdown document to an element, under its own file name.

        This is for a whole document — a runbook, an interface contract, a
        decision note. A sentence describing the element is its `documentation`
        field, not a file; use `update_element` for that.

        The element must already exist, the name must end in `.md` or
        `.markdown`, and one element holds one document per name: attaching
        `runbook.md` twice is refused, and replacing it is `revise_document`.
        """
        return DocumentRead.of(
            await get_documents().attach_text(element_id, filename=filename, content=content)
        )

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_documents(element_id: ElementId) -> list[DocumentSummaryRead]:
        """What is attached to an element: the file names and sizes, not the text.

        Start here rather than with `read_document`: this answer stays small
        whatever the documents weigh, and it carries the id each one is read
        by. An element with nothing attached answers with an empty list; an
        element that does not exist is an error, which is a different answer.
        """
        return [
            DocumentSummaryRead.of(summary)
            for summary in await get_documents().list_for_element(element_id)
        ]

    @server.tool(annotations=READS)
    @speaking_plainly
    async def read_document(document_id: DocumentId) -> DocumentRead:
        """One document with its markdown in full.

        A document may be up to a megabyte of text, so read the one you need
        rather than every document `list_documents` named.
        """
        return DocumentRead.of(await get_documents().get(document_id))

    @server.tool(annotations=EDITS)
    @speaking_plainly
    async def revise_document(
        document_id: DocumentId, filename: Filename, content: Markdown
    ) -> DocumentRead:
        """Replace a document's markdown with a new version of the same file.

        `content` replaces the whole document; there is no partial edit, so
        read it first if you mean to change a paragraph. `filename` must be the
        name the document is already stored under — it is what says you are
        rewriting the file you think you are, and a mismatch is refused rather
        than applied.
        """
        return DocumentRead.of(
            await get_documents().revise_text(document_id, filename=filename, content=content)
        )

    @server.tool(annotations=READS)
    @speaking_plainly
    async def search_documents(
        question: Question,
        element_id: ElementId | None = None,
        limit: SearchLimit = DEFAULT_SEARCH_LIMIT,
    ) -> list[PassageRead]:
        """Find the passages of the attached documents that answer a question.

        This searches by *meaning*, not by keyword, and it answers with
        passages rather than file names: each hit is one section of one
        document, with the trail of headings it sits under and the id of the
        element it is attached to.

        Start here when you do not already know where something is written —
        it is far cheaper than reading documents one after another, and a
        document may be a megabyte of text. `read_document` is then for the
        one you found, when you need the rest of it.

        Pass `element_id` to search only what is written about that element.
        The `score` is a similarity: larger is closer, and hits come back
        closest first.
        """
        return [
            PassageRead.of(passage)
            for passage in await get_documents().search(
                question, element_id=element_id, limit=limit
            )
        ]

    @server.tool(annotations=REMOVES)
    @speaking_plainly
    async def discard_document(document_id: DocumentId) -> str:
        """Detach a document from its element and delete its text.

        There is no undo and no version history: the markdown is gone. Confirm
        with the person you are working for before calling it. Deleting the
        element itself already takes its documents with it.
        """
        await get_documents().discard(document_id)
        return f"document {document_id} was deleted"

    # --- IP addressing ----------------------------------------------------
    # No second store and no second catalogue: a subnet is a
    # `communication_network` element and an address is a property of the
    # element answering on it (docs/adr/0020). These tools exist because the
    # arithmetic — what is free, what is taken, what a prefix keeps for itself
    # — is not something an agent should be asked to do with `update_element`.

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def declare_ip_subnet(
        name: Name,
        cidr: Cidr,
        vrf: Vrf = DEFAULT_VRF,
        reserved: ReservedAddresses = "",
        description: Description = "",
    ) -> SubnetRead:
        """Declare a subnet, so addresses inside it can be handed out.

        This creates a `communication_network` element carrying the prefix, so
        the subnet appears in the catalogue and in every diagram like anything
        else. Write the network itself — `10.0.1.0/24`, not `10.0.1.5/24`,
        which is refused rather than quietly corrected.

        A prefix may be declared once per routing scope, and may sit inside
        another: `10.0.0.0/8` as the corporate range and `10.0.1.0/24` as the
        DMZ is the normal case, and an address then belongs to the narrowest
        one holding it.
        """
        return SubnetRead.of(
            await get_ipam().declare_network(
                name=name, cidr=cidr, vrf=vrf, reserved=reserved, description=description
            )
        )

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_ip_subnets(vrf: Vrf | None = None) -> list[SubnetRead]:
        """Every declared subnet, with how full each one is.

        `capacity`, `reserved`, `used` and `free` are counts and not a
        percentage, so "three addresses left" is sayable — which is the
        sentence somebody acts on.
        """
        return [SubnetRead.of(subnet) for subnet in await get_ipam().list_networks(vrf=vrf)]

    @server.tool(annotations=READS)
    @speaking_plainly
    async def read_ip_subnet(element_id: ElementId) -> SubnetDetailRead:
        """One subnet: what is in it, and the address it would hand out next.

        `element_id` is the subnet's own element id, as `list_ip_subnets`
        reports it. `next_free` is absent when the subnet is full.
        """
        return SubnetDetailRead.of(await get_ipam().read_network(element_id))

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def allocate_ip_address(subnet_id: ElementId, element_id: ElementId) -> AddressRead:
        """Give an element the first address a subnet has free.

        Prefer this to `assign_ip_address` whenever the particular address does
        not matter: it cannot pick one that is taken, reserved, or one the
        prefix keeps for itself. A full subnet is refused rather than served an
        address that is already somebody's.
        """
        return AddressRead.of(await get_ipam().allocate_next(subnet_id, element_id))

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def assign_ip_address(
        element_id: ElementId, address: IpAddressArgument, vrf: Vrf = DEFAULT_VRF
    ) -> AddressRead:
        """Give an element one particular address.

        For an address that is already decided — a gateway, a printer somebody
        wrote on a label. When any free address will do, `allocate_ip_address`
        is the safer call.

        Four things are refused: an element that cannot answer on an address at
        all (a business process has no interface), an address no declared
        subnet holds, one the prefix keeps for itself, and one another element
        already has. Only elements of type node, device, equipment,
        system_software or technology_interface may carry one — a host with two
        NICs is two `technology_interface` elements under one node.
        """
        return AddressRead.of(await get_ipam().assign_address(element_id, address, vrf=vrf))

    @server.tool(annotations=READS)
    @speaking_plainly
    async def locate_ip_address(
        address: IpAddressArgument, vrf: Vrf = DEFAULT_VRF
    ) -> AddressLocationRead:
        """What answers on an address, and what that thing is wired to.

        One call rather than two: the answer names the element and carries the
        sub-graph around it, so "10.0.1.12 is srv-app-01, and Billing runs on
        it" can be written without looking anything else up.
        """
        return AddressLocationRead.of(await get_ipam().locate(address, vrf=vrf))

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_ip_addresses(
        vrf: Vrf | None = None,
        within: Cidr | None = None,
        search: Annotated[str | None, Field(max_length=200)] = None,
    ) -> list[AddressRead]:
        """The inventory: every assigned address, in address order.

        `within` narrows it to a prefix, declared or not — "what is in
        10.0.1.0/26" is answerable whether or not anyone declared that slice.
        `search` matches the element's name.
        """
        return [
            AddressRead.of(assignment)
            for assignment in await get_ipam().list_addresses(vrf=vrf, within=within, search=search)
        ]

    @server.tool(annotations=REMOVES)
    @speaking_plainly
    async def release_ip_address(element_id: ElementId) -> str:
        """Take an element's address back, leaving the element itself alone.

        The address becomes free at once and may be handed to something else,
        so confirm with the person you are working for first: a machine that is
        still running does not stop being reachable because the catalogue
        forgot its address.
        """
        await get_ipam().release_address(element_id)
        return f"element {element_id} no longer holds an IP address"

    # --- Metamodel --------------------------------------------------------

    @server.tool(annotations=READS)
    @speaking_plainly
    async def describe_metamodel() -> MetamodelRead:
        """The ArchiMate 3.2 palette: every element type, relationship type and layer.

        Call this before creating an element or a link. The names it lists are
        the only ones the other tools accept. Each relationship type also
        reports its family, how tightly it binds, and whether a failure travels
        along its arrow or against it.
        """
        return MetamodelRead.snapshot()

    @server.tool(annotations=READS)
    @speaking_plainly
    async def permitted_relationships(
        source: ElementType, target: ElementType
    ) -> list[RelationshipType]:
        """Which relationships may run between two element *types*, strongest first.

        The cheap check before `connect_elements`: an empty answer means
        ArchiMate joins these two types no way at all, and no wording of the
        request will change that.
        """
        return list(permitted_between(source, target))

    @server.tool(annotations=READS)
    @speaking_plainly
    async def relationship_matrix_row(source: ElementType) -> RelationshipMatrixRead:
        """One row of the 61x61 matrix: everything `source` may point at, and how.

        Use it to pick a target type when only the source is settled. Asking
        for a single pair is `permitted_relationships`, and much smaller.
        """
        return RelationshipMatrixRead.for_source(source)

    return server
