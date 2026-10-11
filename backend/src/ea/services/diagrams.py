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

from ea.domain.audit import AuditAction, diagram_view, layout_view
from ea.domain.diagrams import Diagram, DiagramDetail, DiagramNode, check_layout
from ea.domain.errors import (
    DiagramNotFoundError,
    PackageNotFoundError,
    UnknownLayoutElementError,
)
from ea.domain.packages import UNSET, Unset, descendants_of
from ea.services.audit import AuditTrail
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from uuid import UUID

    from ea.domain.audit import AuditEvent
    from ea.domain.ports import DiagramRepository, PackageRepository
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
    ) -> None:
        self._repository = repository
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
            return await self._repository.list_all(root_only=root_only)
        wanted = {package_id}
        if include_subpackages and self._packages is not None:
            everything = {p.id: p for p in await self._packages.list_all()}
            wanted |= descendants_of(package_id, everything)
        return await self._repository.list_all(package_ids=wanted)

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
        event = self._trail.event(
            AuditAction.DIAGRAM_CREATED, entity_id=diagram.id, after=diagram_view(diagram)
        )
        stored = await self._repository.add(diagram, audit=(event,))
        self._audit("diagram created: %r", stored, "diagram_created")
        return stored

    async def get(self, diagram_id: UUID) -> Diagram:
        require_caller()
        diagram = await self._repository.get(diagram_id)
        if diagram is None:
            raise _not_found(diagram_id)
        return diagram

    async def open(self, diagram_id: UUID) -> DiagramDetail:
        """A diagram with the part of the graph it shows, stale boxes left out."""
        require_caller()
        diagram = await self.get(diagram_id)
        nodes = await self._repository.nodes_of(diagram_id)
        graph = await self._architecture.view_of([node.element_id for node in nodes])
        present = {element.id for element in graph.elements}
        return DiagramDetail(
            diagram=diagram,
            nodes=tuple(node for node in nodes if node.element_id in present),
            graph=graph,
        )

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

    async def replace_layout(self, diagram_id: UUID, nodes: Sequence[DiagramNode]) -> None:
        """Replace every box of a diagram, refusing an element the graph lacks.

        A body that places one element twice is refused before anything is
        read; otherwise an unknown diagram is a 404. The elements are checked
        in one graph query, not one per box.
        """
        require_editor()
        layout = check_layout(nodes)
        diagram = await self.get(diagram_id)
        graph = await self._architecture.view_of([node.element_id for node in layout])
        present = {element.id for element in graph.elements}
        missing = [str(node.element_id) for node in layout if node.element_id not in present]
        if missing:
            msg = f"no element with id {', '.join(missing)}"
            raise UnknownLayoutElementError(msg)

        def events(before: tuple[DiagramNode, ...]) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.DIAGRAM_LAYOUT_REPLACED,
                    entity_id=diagram_id,
                    before=layout_view(before),
                    after=layout_view(layout),
                ),
            )

        if not await self._repository.replace_layout(
            diagram_id, layout, now=self._now(), audit=events
        ):
            raise _not_found(diagram_id)  # deleted between the read and the write
        logger.info(
            "diagram %r laid out with %d box(es)",
            diagram.name,
            len(layout),
            extra={
                "action": "diagram_layout_replaced",
                "diagram_id": str(diagram_id),
                "nodes": len(layout),
            },
        )

    @staticmethod
    def _audit(message: str, diagram: Diagram, action: str) -> None:
        logger.info(message, diagram.name, extra={"action": action, "diagram_id": str(diagram.id)})
