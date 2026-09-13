"""Use cases over saved diagrams — ArchiMate views of the graph (docs/adr/0031).

The rule this layer owns is the one no foreign key can state: **a layout may
only place elements the graph holds**. The element is a node in Neo4j and the
box a row in PostgreSQL, so the service asks the architecture service first.
The other half of that missing key is the cascade, which lives where the
deletion does — `ArchitectureService.delete_element`, through
`ElementAttachments`. And because a cascade across two stores can fail, a
reading skips any box whose element is gone rather than showing a hole.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ea.domain.diagrams import Diagram, DiagramDetail, DiagramNode, check_layout
from ea.domain.errors import DiagramNotFoundError, UnknownLayoutElementError
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from uuid import UUID

    from ea.domain.ports import DiagramRepository
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
        clock: Clock = _utc_now,
    ) -> None:
        self._repository = repository
        self._architecture = architecture
        self._now = clock

    async def list_diagrams(self) -> tuple[Diagram, ...]:
        require_caller()
        return await self._repository.list_all()

    async def create(self, *, name: str, description: str = "") -> Diagram:
        require_editor()
        stored = await self._repository.add(
            Diagram.create(name=name, description=description, now=self._now())
        )
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
        self, diagram_id: UUID, *, name: str | None = None, description: str | None = None
    ) -> Diagram:
        require_editor()
        current = await self.get(diagram_id)
        stored = await self._repository.save(
            current.revise(name=name, description=description, now=self._now())
        )
        self._audit("diagram updated: %r", stored, "diagram_updated")
        return stored

    async def delete(self, diagram_id: UUID) -> None:
        require_editor()
        if not await self._repository.delete(diagram_id):
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
        if not await self._repository.replace_layout(diagram_id, layout, now=self._now()):
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
