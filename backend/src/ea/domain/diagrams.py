"""A saved diagram: an ArchiMate *view* over the architecture graph.

A diagram owns no fact (docs/adr/0031). The elements and the relationships it
shows stay in the architecture store; what it records is only *which* elements are drawn and
*where*. That is why removing a box never deletes an element, and why a link
drawn on a diagram is a real relationship written to the graph.

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Final
from uuid import UUID, uuid4

from ea.domain.generated_views import Generation, ViewSource
from ea.domain.model import reject_nul
from ea.domain.packages import UNSET, Unset
from ea.domain.ports import GraphView

if TYPE_CHECKING:
    from ea.domain.diagram_objects import DiagramEdge, DiagramObject

MAX_DIAGRAM_NAME_LENGTH: Final = 200
#: The size a box is drawn at until someone resizes it — the rings' own box.
DEFAULT_BOX_WIDTH: Final = 132.0
DEFAULT_BOX_HEIGHT: Final = 46.0


def validate_diagram_name(name: str) -> str:
    """Trimmed, 1..200 characters, no NUL: the name a diagram can carry."""
    return _clean_name(name)


def _clean_name(name: str) -> str:
    reject_nul(name, "a diagram name")
    cleaned = name.strip()
    if not cleaned:
        msg = "a diagram name cannot be blank"
        raise ValueError(msg)
    if len(cleaned) > MAX_DIAGRAM_NAME_LENGTH:
        msg = f"a diagram name is at most {MAX_DIAGRAM_NAME_LENGTH} characters"
        raise ValueError(msg)
    return cleaned


@dataclass(frozen=True, slots=True)
class DiagramNode:
    """One box: an element, the top-left corner it is drawn at, and its size.

    `parent_object_id` names the group (docs/adr/0063) the box is drawn inside,
    `z` its stacking order. Both are presentation: a group is not an element and
    creates no relationship.
    """

    element_id: UUID
    x: float
    y: float
    width: float = DEFAULT_BOX_WIDTH
    height: float = DEFAULT_BOX_HEIGHT
    parent_object_id: UUID | None = None
    z: int = 0


@dataclass(frozen=True, slots=True)
class Diagram:
    """A named view. `node_count` is read, never written: the rows are the truth."""

    id: UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    node_count: int = 0
    #: Moves on by one at every saved scene: the optimistic-concurrency token (docs/adr/0063).
    revision: int = 0
    #: `None`: drawn by hand. Otherwise a view generated from `source` (docs/adr/0065).
    generation: Generation | None = None
    source: ViewSource | None = None
    #: The package that files this view; `None` is "at the root".
    package_id: UUID | None = None

    @classmethod
    def create(
        cls,
        *,
        name: str,
        now: datetime,
        description: str = "",
        package_id: UUID | None = None,
        generation: Generation | None = None,
        source: ViewSource | None = None,
    ) -> Diagram:
        if (generation is None) != (source is None):
            msg = "a generated view has a source and a generation, a hand-drawn one has neither"
            raise ValueError(msg)
        return cls(
            id=uuid4(),
            name=_clean_name(name),
            description=reject_nul(description, "a diagram description").strip(),
            created_at=now,
            updated_at=now,
            package_id=package_id,
            generation=generation,
            source=source,
        )

    def revise(
        self,
        *,
        now: datetime,
        name: str | None = None,
        description: str | None = None,
        package_id: UUID | Unset | None = UNSET,
    ) -> Diagram:
        """The same diagram under a new name, description or package; an omitted one is kept.

        `package_id=None` is the root; leaving it out (`UNSET`) is "do not move".
        """
        return replace(
            self,
            name=self.name if name is None else _clean_name(name),
            description=self.description
            if description is None
            else reject_nul(description, "a diagram description").strip(),
            package_id=self.package_id if isinstance(package_id, Unset) else package_id,
            updated_at=now,
        )

    def touched(self, now: datetime) -> Diagram:
        return replace(self, updated_at=now)

    def with_node_count(self, node_count: int) -> Diagram:
        return replace(self, node_count=node_count)

    def with_revision(self, revision: int) -> Diagram:
        return replace(self, revision=revision)


@dataclass(frozen=True, slots=True)
class DiagramDetail:
    """A diagram as it is opened: its boxes, and the part of the graph they show."""

    diagram: Diagram
    nodes: tuple[DiagramNode, ...]
    graph: GraphView
    objects: tuple[DiagramObject, ...] = ()
    edges: tuple[DiagramEdge, ...] = ()
    #: On the `as_of` day asked, what is out of period - greyed by the SPA, never removed.
    inactive_elements: frozenset[UUID] = frozenset()
    inactive_relationships: frozenset[UUID] = frozenset()


def check_layout(nodes: Sequence[DiagramNode]) -> tuple[DiagramNode, ...]:
    """A layout places each element once — a view shows a concept, not copies of it."""
    seen: set[UUID] = set()
    for node in nodes:
        if node.element_id in seen:
            msg = f"element {node.element_id} is placed twice on the diagram"
            raise ValueError(msg)
        seen.add(node.element_id)
    return tuple(nodes)
