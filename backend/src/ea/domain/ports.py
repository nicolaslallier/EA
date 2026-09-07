"""The ports the domain declares and the infrastructure implements.

`repositories/` depends on this module; this module depends on nothing. That is
the arrow `services -> domain <- repositories` from `CLAUDE.md`, made explicit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ea.domain.archimate import ElementType, Layer, RelationshipType
from ea.domain.model import Element, Relationship


@dataclass(frozen=True, slots=True)
class GraphView:
    """A connected slice of the repository: the nodes and the edges between them.

    Returned by every traversal, so the caller never has to stitch two lists
    together to draw a diagram.
    """

    elements: tuple[Element, ...]
    relationships: tuple[Relationship, ...]

    @property
    def is_empty(self) -> bool:
        return not self.elements


@dataclass(frozen=True, slots=True)
class ElementFilter:
    """The ways the catalogue can be narrowed. All criteria are combined with AND."""

    element_types: tuple[ElementType, ...] = ()
    layers: tuple[Layer, ...] = ()
    search: str | None = None
    limit: int = 50
    offset: int = 0


class ArchitectureRepository(Protocol):
    """Persistence for the architecture graph.

    Every method is async because the only implementation talks to Neo4j over
    Bolt; a synchronous in-memory double satisfying this protocol would still
    have to declare `async def`.
    """

    async def add_element(self, element: Element) -> Element: ...

    async def get_element(self, element_id: UUID) -> Element | None: ...

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]: ...

    async def count_elements(self, criteria: ElementFilter) -> int: ...

    async def save_element(self, element: Element) -> Element:
        """Persist an already-existing element, overwriting its stored form."""
        ...

    async def delete_element(self, element_id: UUID) -> bool:
        """Remove an element and every relationship attached to it."""
        ...

    async def add_relationship(self, relationship: Relationship) -> Relationship: ...

    async def get_relationship(self, relationship_id: UUID) -> Relationship | None: ...

    async def list_relationships(
        self,
        *,
        element_id: UUID | None = None,
        relationship_types: Sequence[RelationshipType] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Relationship, ...]: ...

    async def delete_relationship(self, relationship_id: UUID) -> bool: ...

    async def neighbourhood(
        self,
        element_id: UUID,
        *,
        depth: int = 1,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        """Everything reachable from an element within `depth` hops, either way."""
        ...

    async def impacted_by(
        self,
        element_id: UUID,
        *,
        depth: int = 5,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        """What depends on this element — the answer to "what breaks if it fails".

        Follows relationships *against* their direction: if an application
        service serves a business process, the process is impacted by the
        service, not the reverse.
        """
        ...

    async def would_close_a_containment_cycle(self, source_id: UUID, target_id: UUID) -> bool:
        """Whether composing `target` under `source` would make containment cyclic."""
        ...
