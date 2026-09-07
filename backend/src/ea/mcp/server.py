"""The tools an agent may call, and what each one is allowed to do.

One tool per use case the `ArchitectureService` already exposes — the same
list `api/architecture.py` and `api/metamodel.py` serve over HTTP, in the same
order, so the two adapters can be read side by side. Nothing here decides
anything: a tool binds its arguments, calls the service, and renders the answer
with the very models the REST API renders, so an agent and the SPA are told the
same thing about the same element.

The service is fetched through a callable rather than held, because the
application builds it during its lifespan and this module is assembled before
that: see `main.create_app`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Final
from uuid import UUID

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ea.api.schemas import (
    ElementPage,
    ElementRead,
    GraphRead,
    MetamodelRead,
    RelationshipMatrixRead,
    RelationshipRead,
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
from ea.domain.ports import ElementFilter
from ea.mcp.errors import speaking_plainly
from ea.repositories.archimate_graph import MAX_TRAVERSAL_DEPTH
from ea.services.architecture import ArchitectureService

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
direction the arrow is drawn.\
"""

#: The service the tools call, looked up per call. See the module docstring.
ServiceProvider = Callable[[], ArchitectureService]

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
Limit = Annotated[int, Field(ge=1, le=200)]
Offset = Annotated[int, Field(ge=0)]

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


def build_mcp_server(get_service: ServiceProvider, *, version: str = "0.1.0") -> MCPServer[Any]:
    """Assemble the tool set over one architecture service.

    Taking a provider rather than the service keeps this callable before the
    application has opened its database, and lets a test hand over the same
    in-memory double the API tests use.
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
