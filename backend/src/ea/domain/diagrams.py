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
from typing import Final
from uuid import UUID, uuid4

from ea.domain.ports import GraphView

MAX_DIAGRAM_NAME_LENGTH: Final = 200


def _clean_name(name: str) -> str:
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
    """One box: an element, and the top-left corner it is drawn at."""

    element_id: UUID
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Diagram:
    """A named view. `node_count` is read, never written: the rows are the truth."""

    id: UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    node_count: int = 0

    @classmethod
    def create(cls, *, name: str, now: datetime, description: str = "") -> Diagram:
        return cls(
            id=uuid4(),
            name=_clean_name(name),
            description=description.strip(),
            created_at=now,
            updated_at=now,
        )

    def revise(
        self, *, now: datetime, name: str | None = None, description: str | None = None
    ) -> Diagram:
        """The same diagram under a new name or description; an omitted one is kept."""
        return replace(
            self,
            name=self.name if name is None else _clean_name(name),
            description=self.description if description is None else description.strip(),
            updated_at=now,
        )

    def touched(self, now: datetime) -> Diagram:
        return replace(self, updated_at=now)

    def with_node_count(self, node_count: int) -> Diagram:
        return replace(self, node_count=node_count)


@dataclass(frozen=True, slots=True)
class DiagramDetail:
    """A diagram as it is opened: its boxes, and the part of the graph they show."""

    diagram: Diagram
    nodes: tuple[DiagramNode, ...]
    graph: GraphView


def check_layout(nodes: Sequence[DiagramNode]) -> tuple[DiagramNode, ...]:
    """A layout places each element once — a view shows a concept, not copies of it."""
    seen: set[UUID] = set()
    for node in nodes:
        if node.element_id in seen:
            msg = f"element {node.element_id} is placed twice on the diagram"
            raise ValueError(msg)
        seen.add(node.element_id)
    return tuple(nodes)
