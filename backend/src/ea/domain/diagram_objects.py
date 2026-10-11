"""What a diagram draws that is not an element: notes, groups, and the bends of a link.

All of it is presentation (docs/adr/0063). A **group is not an
element of the graph and never creates a relationship**: putting three boxes in
"Domaine Paiement" says "visually together" and nothing else. A note anchored
to an element is a dotted line, not a relationship. A link keeps being the
relationship of the graph; only its stroke — the bends — is the view's.

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from ea.domain.diagrams import DiagramNode, check_layout

#: How many groups may contain one another — a note may sit in the deepest.
MAX_GROUP_DEPTH: Final = 5
#: Bends a single link may carry.
MAX_WAYPOINTS: Final = 20
MAX_OBJECT_TEXT: Final = 2000


class ObjectKind(StrEnum):
    NOTE = "note"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class DiagramObject:
    """A note (its text is the body) or a group (its text is the title)."""

    id: UUID
    kind: ObjectKind
    x: float
    y: float
    width: float
    height: float
    text: str = ""
    colour: str | None = None
    z: int = 0
    parent_id: UUID | None = None
    anchor_element_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DiagramEdge:
    """The stroke of a link whose route was changed. No row = a straight segment."""

    relationship_id: UUID
    waypoints: tuple[Point, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagramScene:
    nodes: tuple[DiagramNode, ...] = ()
    objects: tuple[DiagramObject, ...] = ()
    edges: tuple[DiagramEdge, ...] = ()


def _groups_above(
    start: UUID | None, by_id: Mapping[UUID, DiagramObject], *, owner: UUID | None
) -> int:
    """How many groups lie between `start` and the top; refuses a loop or a non-group."""
    depth = 0
    seen: set[UUID] = set()
    current = start
    while current is not None:
        if current in seen or current == owner:
            msg = "groups cannot contain one another in a loop"
            raise ValueError(msg)
        seen.add(current)
        parent = by_id.get(current)
        if parent is None:
            msg = f"no group {current} on the diagram"
            raise ValueError(msg)
        if parent.kind is not ObjectKind.GROUP:
            msg = "an object can only be placed in a group"
            raise ValueError(msg)
        depth += 1
        current = parent.parent_id
    return depth


def check_scene(
    nodes: Sequence[DiagramNode],
    objects: Sequence[DiagramObject],
    edges: Sequence[DiagramEdge],
) -> DiagramScene:
    """The rules of a scene that need no store: each concept once, groups that nest sanely.

    Whether the elements and the relationships exist is the service's question.
    """
    layout = check_layout(nodes)
    by_id: dict[UUID, DiagramObject] = {}
    for item in objects:
        if item.id in by_id:
            msg = f"object {item.id} appears twice on the diagram"
            raise ValueError(msg)
        if len(item.text) > MAX_OBJECT_TEXT:
            msg = f"the text of an object is at most {MAX_OBJECT_TEXT} characters"
            raise ValueError(msg)
        if not all(math.isfinite(v) for v in (item.x, item.y, item.width, item.height)):
            msg = "the position and size of an object are finite numbers"
            raise ValueError(msg)
        if item.kind is ObjectKind.GROUP and item.anchor_element_id is not None:
            msg = "a group cannot be anchored to an element"
            raise ValueError(msg)
        by_id[item.id] = item
    placed = {node.element_id for node in layout}
    for item in objects:
        if item.anchor_element_id is not None and item.anchor_element_id not in placed:
            msg = f"a note is anchored to {item.anchor_element_id}, which is not on the diagram"
            raise ValueError(msg)
        # A group nested five deep is the limit, so a group itself may have
        # only four groups above it; a note or a box may have five.
        limit = MAX_GROUP_DEPTH - (1 if item.kind is ObjectKind.GROUP else 0)
        if _groups_above(item.parent_id, by_id, owner=item.id) > limit:
            msg = f"groups nest at most {MAX_GROUP_DEPTH} deep"
            raise ValueError(msg)
    for node in layout:
        if _groups_above(node.parent_object_id, by_id, owner=None) > MAX_GROUP_DEPTH:
            msg = f"groups nest at most {MAX_GROUP_DEPTH} deep"
            raise ValueError(msg)
    routed: set[UUID] = set()
    for edge in edges:
        if edge.relationship_id in routed:
            msg = f"relationship {edge.relationship_id} is routed twice"
            raise ValueError(msg)
        routed.add(edge.relationship_id)
        if len(edge.waypoints) > MAX_WAYPOINTS:
            msg = f"a link has at most {MAX_WAYPOINTS} bends"
            raise ValueError(msg)
        if not all(math.isfinite(p.x) and math.isfinite(p.y) for p in edge.waypoints):
            msg = "the bends of a link are finite numbers"
            raise ValueError(msg)
    return DiagramScene(nodes=layout, objects=tuple(objects), edges=tuple(edges))
