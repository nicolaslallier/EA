"""Use cases over saved diagrams — ArchiMate views of the graph (docs/adr/0031).

The rule this layer owns: **a layout may only place elements the graph
holds**. A foreign key states it since docs/adr/0033, and takes the boxes of a
deleted element; the service still asks the architecture service first, so a
missing element is a readable refusal rather than a foreign-key violation.

A reading still skips any box whose element is gone rather than showing a
hole. With the foreign key that only guards a race — an element deleted between
the read of the diagram's boxes and the read of its elements — but that race
is real, and a hole is what it would draw.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ea.domain.audit import AuditAction, diagram_view, scene_views
from ea.domain.diagram_objects import DiagramEdge, DiagramObject, DiagramScene, check_scene
from ea.domain.diagrams import MAX_DIAGRAMS, Diagram, DiagramDetail, DiagramNode, check_layout
from ea.domain.errors import (
    DiagramHasAnnotationsError,
    DiagramLiveError,
    DiagramNotFoundError,
    DiagramNotGeneratedError,
    DiagramNotLiveError,
    GeneratedViewsUnavailableError,
    PackageNotFoundError,
    ScopeTooLargeError,
    UnknownLayoutElementError,
)
from ea.domain.generated_views import (
    MAX_GENERATED_MEMBERS,
    Generation,
    SourceDiff,
    ViewSource,
    diff_members,
    resolve_members,
)
from ea.domain.packages import UNSET, Unset, descendants_of
from ea.domain.timeline import Lifespan
from ea.services.audit import AuditTrail
from ea.services.caller import logged, require_caller, require_editor

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from ea.domain.audit import AuditEvent
    from ea.domain.ports import DiagramRepository, GraphView, PackageRepository, ViewSourceResolver
    from ea.services.architecture import ArchitectureService

Clock = Callable[[], datetime]

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _not_found(diagram_id: UUID) -> DiagramNotFoundError:
    return DiagramNotFoundError(f"no diagram with id {diagram_id}")


class DiagramService:
    """The single entry point `api/` uses to list, open and change diagrams."""

    def __init__(
        self,
        repository: DiagramRepository,
        architecture: ArchitectureService,
        *,
        packages: PackageRepository | None = None,
        clock: Clock = _utc_now,
        sources: ViewSourceResolver | None = None,
    ) -> None:
        self._repository = repository
        self._sources = sources
        self._architecture = architecture
        self._packages = packages
        self._now = clock
        self._trail = AuditTrail(clock)

    async def list_diagrams(
        self,
        *,
        package_id: UUID | None = None,
        include_subpackages: bool = True,
        root_only: bool = False,
    ) -> tuple[Diagram, ...]:
        require_caller()
        if root_only and package_id is not None:
            msg = "a diagram cannot be both at the root and in a package"
            raise ValueError(msg)
        if package_id is None:
            return self._within_the_cap(await self._repository.list_all(root_only=root_only))
        wanted = {package_id}
        if include_subpackages and self._packages is not None:
            everything = {p.id: p for p in await self._packages.list_all()}
            wanted |= descendants_of(package_id, everything)
        return self._within_the_cap(await self._repository.list_all(package_ids=wanted))

    @staticmethod
    def _within_the_cap(listed: tuple[Diagram, ...]) -> tuple[Diagram, ...]:
        if len(listed) > MAX_DIAGRAMS:
            msg = f"more than {MAX_DIAGRAMS} diagrams: narrow the listing to a package"
            raise ScopeTooLargeError(msg)
        return listed

    async def _require_package(self, package_id: UUID | None) -> None:
        if package_id is None:
            return
        if self._packages is None or await self._packages.get(package_id) is None:
            msg = f"no package with id {package_id}"
            raise PackageNotFoundError(msg)

    async def create(
        self, *, name: str, description: str = "", package_id: UUID | None = None
    ) -> Diagram:
        require_editor()
        await self._require_package(package_id)
        diagram = Diagram.create(
            name=name, description=description, now=self._now(), package_id=package_id
        )

        def created(stored: Diagram) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.DIAGRAM_CREATED, entity_id=stored.id, after=diagram_view(stored)
                ),
            )

        stored = await self._repository.add(diagram, audit=created)
        self._audit("diagram created: %r", stored, "diagram_created")
        return stored

    async def get(self, diagram_id: UUID) -> Diagram:
        require_caller()
        diagram = await self._repository.get(diagram_id)
        if diagram is None:
            raise _not_found(diagram_id)
        return diagram

    async def open(
        self, diagram_id: UUID, *, as_of: date | None = None, link_limit: int | None = None
    ) -> DiagramDetail:
        """A diagram with the part of the graph it shows, stale boxes left out.

        `as_of` removes nothing - a diagram is a view, and a box out of period keeps
        its place in the layout (docs/adr/0060) - it only says which ones are not in
        service that day, for the SPA to grey. `link_limit` stops the read of the links in
        SQL (a report asks for one more than it accepts).
        """
        require_caller()
        diagram = await self.get(diagram_id)
        if diagram.generation is Generation.LIVE:
            # No box is stored: who is on it is what the source yields now (docs/adr/0071).
            members = await self._members_of(diagram)
            graph = await self._architecture.view_of(members)
            live_elements, live_links = self._out_of_period(graph, as_of)
            return DiagramDetail(
                diagram=diagram,
                nodes=(),
                graph=graph,
                inactive_elements=live_elements,
                inactive_relationships=live_links,
            )
        nodes = await self._repository.nodes_of(diagram_id)
        objects = await self._repository.objects_of(diagram_id)
        edges = await self._repository.edges_of(diagram_id)
        graph = await self._architecture.view_of(
            [node.element_id for node in nodes], link_limit=link_limit
        )
        present = {element.id for element in graph.elements}
        linked = {link.id for link in graph.relationships}
        inactive_elements, inactive_links = self._out_of_period(graph, as_of)
        return DiagramDetail(
            diagram=diagram,
            nodes=tuple(node for node in nodes if node.element_id in present),
            graph=graph,
            objects=objects,
            edges=tuple(edge for edge in edges if edge.relationship_id in linked),
            inactive_elements=inactive_elements,
            inactive_relationships=inactive_links,
        )

    @staticmethod
    def _out_of_period(
        graph: GraphView, as_of: date | None
    ) -> tuple[frozenset[UUID], frozenset[UUID]]:
        """Which elements and links are not in service on `as_of` (nothing, without a day)."""
        if as_of is None:
            return frozenset(), frozenset()
        inactive_elements = frozenset(
            e.id for e in graph.elements if not Lifespan.read_tolerant(e.properties).covers(as_of)
        )
        inactive_links = frozenset(
            link.id
            for link in graph.relationships
            if not Lifespan.read_tolerant(link.properties).covers(as_of)
            or link.source_id in inactive_elements
            or link.target_id in inactive_elements
        )
        return inactive_elements, inactive_links

    async def update(
        self,
        diagram_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        package_id: UUID | Unset | None = UNSET,
    ) -> Diagram:
        require_editor()
        current = await self.get(diagram_id)
        if not isinstance(package_id, Unset):
            await self._require_package(package_id)
        revised = current.revise(
            name=name, description=description, package_id=package_id, now=self._now()
        )

        def events(before: Diagram) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.DIAGRAM_UPDATED,
                    entity_id=diagram_id,
                    before=diagram_view(before),
                    after=diagram_view(revised),
                ),
            )

        stored = await self._repository.save(revised, audit=events)
        self._audit("diagram updated: %r", stored, "diagram_updated")
        return stored

    async def delete(self, diagram_id: UUID) -> None:
        require_editor()

        def events(before: Diagram) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.DIAGRAM_DELETED,
                    entity_id=diagram_id,
                    before=diagram_view(before),
                ),
            )

        if not await self._repository.delete(diagram_id, audit=events):
            raise _not_found(diagram_id)
        logger.info(
            "diagram deleted", extra={"action": "diagram_deleted", "diagram_id": str(diagram_id)}
        )

    async def replace_layout(
        self,
        diagram_id: UUID,
        *,
        revision: int,
        nodes: Sequence[DiagramNode],
        objects: Sequence[DiagramObject] = (),
        edges: Sequence[DiagramEdge] = (),
    ) -> int:
        """Replace the whole scene of a diagram, against the revision the caller saw.

        A scene that breaks `check_scene` is refused before anything is read;
        an unknown diagram is a 404; an element the graph lacks, or a route for
        a link that does not join two placed elements, is refused; a stale
        `revision` is a conflict. The elements and links are checked in one
        graph query, not one per box. Returns the new revision.
        """
        require_editor()
        scene = check_scene(nodes, objects, edges)
        diagram = await self.get(diagram_id)
        if diagram.generation is Generation.LIVE:
            msg = "a live view is recomputed at every opening and cannot be edited: freeze it"
            raise DiagramLiveError(msg)
        graph = await self._architecture.view_of([node.element_id for node in scene.nodes])
        present = {element.id for element in graph.elements}
        missing = [str(node.element_id) for node in scene.nodes if node.element_id not in present]
        if missing:
            msg = f"no element with id {', '.join(missing)}"
            raise UnknownLayoutElementError(msg)
        joined = {link.id for link in graph.relationships}
        stray = [
            str(edge.relationship_id) for edge in scene.edges if edge.relationship_id not in joined
        ]
        if stray:
            msg = f"no relationship between two elements of the diagram: {', '.join(stray)}"
            raise ValueError(msg)

        def events(before: DiagramScene) -> tuple[AuditEvent, ...]:
            old, new = scene_views(before, scene, revision=revision)
            return (
                self._trail.event(
                    AuditAction.DIAGRAM_LAYOUT_REPLACED,
                    entity_id=diagram_id,
                    before=old,
                    after=new,
                ),
            )

        stored = await self._repository.replace_layout(
            diagram_id, scene, revision=revision, now=self._now(), audit=events
        )
        if stored is None:
            raise _not_found(diagram_id)  # deleted between the read and the write
        # Counts only: the text of a note is the author's, and may be sensitive.
        logger.info(
            "diagram %r saved at revision %d",
            logged(diagram.name),
            stored,
            extra={
                "action": "diagram_layout_replaced",
                "diagram_id": str(diagram_id),
                "diagram_revision": stored,
                "nodes": len(scene.nodes),
                "objects": len(scene.objects),
                "edges": len(scene.edges),
            },
        )
        return stored

    # --- Generated views (docs/adr/0071) -------------------------------------

    def _resolver(self) -> ViewSourceResolver:
        if self._sources is None:
            msg = "generated views need the queries and packages, which this deployment lacks"
            raise GeneratedViewsUnavailableError(msg)
        return self._sources

    async def _members_of(self, diagram: Diagram) -> tuple[UUID, ...]:
        """What the source of a generated view yields now: at most the ceiling, else refused."""
        if diagram.source is None:
            raise DiagramNotGeneratedError("this diagram was drawn by hand: it has no source")
        found = await self._resolver().members(diagram.source, limit=MAX_GENERATED_MEMBERS)
        return resolve_members(found)

    async def generate(self, *, name: str, source: ViewSource) -> DiagramDetail:
        """A new live view of everything a query or a package yields.

        The source is read once now, to refuse a missing one and one too big for a
        diagram; nothing of it is stored but the reference.
        """
        require_editor()
        resolve_members(await self._resolver().members(source, limit=MAX_GENERATED_MEMBERS))
        diagram = Diagram.create(
            name=name, now=self._now(), generation=Generation.LIVE, source=source
        )

        def generated(stored: Diagram) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.DIAGRAM_GENERATED, entity_id=stored.id, after=diagram_view(stored)
                ),
            )

        stored = await self._repository.add(diagram, audit=generated)
        self._audit("generated view created: %r", stored, "diagram_generated")
        return await self.open(stored.id)

    async def source_diff(self, diagram_id: UUID) -> SourceDiff:
        """What a frozen view would gain and lose if it followed its source. Writes nothing."""
        require_caller()
        diagram = await self._frozen(diagram_id)
        stored = [node.element_id for node in await self._repository.nodes_of(diagram_id)]
        added, removed = diff_members(stored, await self._members_of(diagram))
        return SourceDiff(added=(await self._architecture.view_of(added)).elements, removed=removed)

    async def freeze(
        self, diagram_id: UUID, nodes: Sequence[DiagramNode], *, revision: int
    ) -> Diagram:
        """Store the layout the SPA computed and make the view an ordinary editable diagram.

        Against the revision the caller read: a view somebody else froze or thawed meanwhile
        is a conflict, not an overwrite.
        """
        require_editor()
        layout = check_layout(nodes)
        diagram = await self.get(diagram_id)
        if diagram.generation is None:
            raise DiagramNotGeneratedError("this diagram was drawn by hand: it has no source")
        if diagram.generation is Generation.FROZEN:
            raise DiagramNotLiveError("this view is already frozen")
        members = set(await self._members_of(diagram))
        strangers = [n for n in layout if n.element_id not in members]
        if strangers:
            msg = f"{len(strangers)} box(es) are not in the source of this view"
            raise ValueError(msg)

        def frozen(before: Diagram) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.DIAGRAM_FROZEN,
                    entity_id=diagram_id,
                    before=diagram_view(before),
                    after={**diagram_view(before), "generation": "frozen", "nodes": len(layout)},
                ),
            )

        stored = await self._repository.freeze(
            diagram_id, layout, revision=revision, now=self._now(), audit=frozen
        )
        if stored is None:
            raise _not_found(diagram_id)
        self._audit("view frozen: %r", diagram, "diagram_frozen")
        return await self.get(diagram_id)

    async def unfreeze(
        self, diagram_id: UUID, *, revision: int, discard_annotations: bool = False
    ) -> Diagram:
        """Back to a live view. Notes, groups and custom routes are not silently lost."""
        require_editor()
        diagram = await self._frozen(diagram_id)
        annotated = bool(
            await self._repository.objects_of(diagram_id)
            or await self._repository.edges_of(diagram_id)
        )
        if annotated and not discard_annotations:
            msg = "this view carries notes, groups or routed links: thawing would delete them"
            raise DiagramHasAnnotationsError(msg)

        def thawed(before: Diagram) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.DIAGRAM_UNFROZEN,
                    entity_id=diagram_id,
                    before=diagram_view(before),
                    after={**diagram_view(before), "generation": "live"},
                ),
            )

        stored = await self._repository.unfreeze(
            diagram_id, revision=revision, now=self._now(), audit=thawed
        )
        if stored is None:
            raise _not_found(diagram_id)
        self._audit("view thawed: %r", diagram, "diagram_unfrozen")
        return await self.get(diagram_id)

    async def _frozen(self, diagram_id: UUID) -> Diagram:
        diagram = await self.get(diagram_id)
        if diagram.generation is None:
            raise DiagramNotGeneratedError("this diagram was drawn by hand: it has no source")
        if diagram.generation is Generation.LIVE:
            msg = "this view is live: it has no stored boxes to refresh or thaw"
            raise DiagramLiveError(msg)
        return diagram

    @staticmethod
    def _audit(message: str, diagram: Diagram, action: str) -> None:
        logger.info(
            message, logged(diagram.name), extra={"action": action, "diagram_id": str(diagram.id)}
        )
