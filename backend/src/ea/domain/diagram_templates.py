"""Diagram templates: recurring schemas as a typed skeleton, with no element in it.

A template says "an application component inside a zone, realised by a node,
with this note beside it" — slots of an ArchiMate type, groups, notes, links
between slots by relationship type. It holds **no element**: instantiating it
binds each slot to an existing element of the right type, or to one to create,
or leaves it out (docs/adr/0066). The body is a versioned
document validated here, against the metamodel the domain already holds — no
list of types or relationships is restated.

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from ea.domain.archimate import ElementType, RelationshipType, permitted_relationships
from ea.domain.diagram_objects import MAX_GROUP_DEPTH, MAX_OBJECT_TEXT, DiagramScene, ObjectKind
from ea.domain.model import Element, Relationship

TEMPLATE_VERSION: Final = 1
MAX_TEMPLATE_NAME: Final = 200
MAX_SLOTS: Final = 50
MAX_TEMPLATE_OBJECTS: Final = 100
MAX_TEMPLATE_LINKS: Final = 100
#: How many templates the library holds: it is listed whole, by the API and by an agent.
MAX_TEMPLATES: Final = 200
_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
_HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True, slots=True)
class Slot:
    """Where an element of `element_type` goes. Its `label` says what it is for."""

    key: str
    element_type: ElementType
    label: str
    x: float
    y: float
    width: float
    height: float
    stereotype: str | None = None
    parent_key: str | None = None


@dataclass(frozen=True, slots=True)
class TemplateGroup:
    key: str
    text: str
    x: float
    y: float
    width: float
    height: float
    colour: str | None = None
    parent_key: str | None = None


@dataclass(frozen=True, slots=True)
class TemplateNote:
    key: str
    text: str
    x: float
    y: float
    width: float
    height: float
    colour: str | None = None
    parent_key: str | None = None
    anchor_key: str | None = None


@dataclass(frozen=True, slots=True)
class TemplateLink:
    source_key: str
    target_key: str
    relationship_type: RelationshipType


@dataclass(frozen=True, slots=True)
class TemplateBody:
    version: int
    slots: tuple[Slot, ...] = ()
    groups: tuple[TemplateGroup, ...] = ()
    notes: tuple[TemplateNote, ...] = ()
    links: tuple[TemplateLink, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagramTemplate:
    id: UUID
    name: str
    description: str
    body: TemplateBody
    created_at: datetime
    updated_at: datetime


def clean_template_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_TEMPLATE_NAME:
        msg = f"a template name is between 1 and {MAX_TEMPLATE_NAME} characters"
        raise ValueError(msg)
    return cleaned


def _groups_above(start: str | None, groups: Mapping[str, TemplateGroup], owner: str | None) -> int:
    depth, seen, current = 0, set[str](), start
    while current is not None:
        if current in seen or current == owner:
            msg = "groups cannot contain one another in a loop"
            raise ValueError(msg)
        seen.add(current)
        parent = groups.get(current)
        if parent is None:
            msg = f"{current!r} is not a group of this template"
            raise ValueError(msg)
        depth += 1
        current = parent.parent_key
    return depth


def check_body(body: TemplateBody) -> None:
    """The rules of a body that need no store: keys, nesting, anchors, and the metamodel."""
    if body.version != TEMPLATE_VERSION:
        msg = f"the template version must be {TEMPLATE_VERSION}"
        raise ValueError(msg)
    if len(body.slots) > MAX_SLOTS or len(body.groups) + len(body.notes) > MAX_TEMPLATE_OBJECTS:
        msg = "a template has too many slots or objects"
        raise ValueError(msg)
    if len(body.links) > MAX_TEMPLATE_LINKS:
        msg = f"a template has at most {MAX_TEMPLATE_LINKS} links"
        raise ValueError(msg)
    boxes: list[Slot | TemplateGroup | TemplateNote] = [*body.slots, *body.groups, *body.notes]
    if not all(math.isfinite(v) for b in boxes for v in (b.x, b.y, b.width, b.height)):
        msg = "the position and size of a template box are finite numbers"
        raise ValueError(msg)
    keys = [
        *(s.key for s in body.slots),
        *(g.key for g in body.groups),
        *(n.key for n in body.notes),
    ]
    if len(set(keys)) != len(keys):
        msg = "a key is used twice in the template"
        raise ValueError(msg)
    for key in keys:
        if not _KEY.match(key):
            msg = f"{key!r} is not a valid key"
            raise ValueError(msg)
    groups = {g.key: g for g in body.groups}
    for slot in body.slots:
        if slot.element_type.is_connector:
            msg = "a slot cannot be a connector"
            raise ValueError(msg)
        if _groups_above(slot.parent_key, groups, None) > MAX_GROUP_DEPTH:
            msg = f"groups nest at most {MAX_GROUP_DEPTH} deep"
            raise ValueError(msg)
    for group in body.groups:
        if _groups_above(group.parent_key, groups, group.key) > MAX_GROUP_DEPTH - 1:
            msg = f"groups nest at most {MAX_GROUP_DEPTH} deep"
            raise ValueError(msg)
    slot_by_key = {s.key: s for s in body.slots}
    texts: list[TemplateGroup | TemplateNote] = [*body.groups, *body.notes]
    for item in texts:
        if len(item.text) > MAX_OBJECT_TEXT:
            msg = f"the text of an object is at most {MAX_OBJECT_TEXT} characters"
            raise ValueError(msg)
        if item.colour is not None and not _HEX_COLOUR.match(item.colour):
            msg = "a colour is #rrggbb"
            raise ValueError(msg)
    for note in body.notes:
        if _groups_above(note.parent_key, groups, None) > MAX_GROUP_DEPTH:
            msg = f"groups nest at most {MAX_GROUP_DEPTH} deep"
            raise ValueError(msg)
        if note.anchor_key is not None and note.anchor_key not in slot_by_key:
            msg = f"a note is anchored on {note.anchor_key!r}, which is not a slot"
            raise ValueError(msg)
    seen: set[tuple[str, str, RelationshipType]] = set()
    for link in body.links:
        source, target = slot_by_key.get(link.source_key), slot_by_key.get(link.target_key)
        if source is None or target is None:
            msg = (
                f"a link joins {link.source_key!r} and {link.target_key!r}, "
                "which must both be slots"
            )
            raise ValueError(msg)
        if link.relationship_type not in permitted_relationships(
            source.element_type, target.element_type
        ):
            msg = (
                f"{link.relationship_type.value} is not allowed between "
                f"{source.element_type.value} and {target.element_type.value}"
            )
            raise ValueError(msg)
        triple = (link.source_key, link.target_key, link.relationship_type)
        if triple in seen:
            msg = "the same link is declared twice"
            raise ValueError(msg)
        seen.add(triple)


def _refuse_oversized(raw: Mapping[str, Any]) -> None:
    """Count before building: a stored or imported body is bounded when read, not after."""
    limits = (
        ("slots", MAX_SLOTS),
        ("groups", MAX_TEMPLATE_OBJECTS),
        ("notes", MAX_TEMPLATE_OBJECTS),
        ("links", MAX_TEMPLATE_LINKS),
    )
    for key, limit in limits:
        items = raw.get(key)
        if isinstance(items, (list, tuple)) and len(items) > limit:
            msg = f"a template has at most {limit} {key}"
            raise ValueError(msg)


def parse_body(raw: Mapping[str, Any]) -> TemplateBody:
    """A body from its JSON form — the same validators for a manual write and an import."""
    _refuse_oversized(raw)
    try:
        body = TemplateBody(
            version=raw["version"],
            slots=tuple(
                Slot(
                    key=s["key"],
                    element_type=ElementType(s["element_type"]),
                    label=s["label"],
                    x=float(s["x"]),
                    y=float(s["y"]),
                    width=float(s["width"]),
                    height=float(s["height"]),
                    stereotype=s.get("stereotype"),
                    parent_key=s.get("parent_key"),
                )
                for s in raw["slots"]
            ),
            groups=tuple(
                TemplateGroup(
                    key=g["key"],
                    text=g["text"],
                    x=float(g["x"]),
                    y=float(g["y"]),
                    width=float(g["width"]),
                    height=float(g["height"]),
                    colour=g.get("colour"),
                    parent_key=g.get("parent_key"),
                )
                for g in raw["groups"]
            ),
            notes=tuple(
                TemplateNote(
                    key=n["key"],
                    text=n["text"],
                    x=float(n["x"]),
                    y=float(n["y"]),
                    width=float(n["width"]),
                    height=float(n["height"]),
                    colour=n.get("colour"),
                    parent_key=n.get("parent_key"),
                    anchor_key=n.get("anchor_key"),
                )
                for n in raw["notes"]
            ),
            links=tuple(
                TemplateLink(
                    item["source_key"],
                    item["target_key"],
                    RelationshipType(item["relationship_type"]),
                )
                for item in raw["links"]
            ),
        )
    except (KeyError, TypeError, AttributeError) as error:
        msg = f"the template body lacks or misuses {error}"
        raise ValueError(msg) from error
    check_body(body)
    return body


def body_to_json(body: TemplateBody) -> dict[str, Any]:
    return {
        "version": body.version,
        "slots": [
            {
                "key": s.key,
                "element_type": s.element_type.value,
                "label": s.label,
                "x": s.x,
                "y": s.y,
                "width": s.width,
                "height": s.height,
                "stereotype": s.stereotype,
                "parent_key": s.parent_key,
            }
            for s in body.slots
        ],
        "groups": [
            {
                "key": g.key,
                "text": g.text,
                "x": g.x,
                "y": g.y,
                "width": g.width,
                "height": g.height,
                "colour": g.colour,
                "parent_key": g.parent_key,
            }
            for g in body.groups
        ],
        "notes": [
            {
                "key": n.key,
                "text": n.text,
                "x": n.x,
                "y": n.y,
                "width": n.width,
                "height": n.height,
                "colour": n.colour,
                "parent_key": n.parent_key,
                "anchor_key": n.anchor_key,
            }
            for n in body.notes
        ],
        "links": [
            {
                "source_key": link.source_key,
                "target_key": link.target_key,
                "relationship_type": link.relationship_type.value,
            }
            for link in body.links
        ],
    }


def template_from_scene(
    scene: DiagramScene,
    elements: Mapping[UUID, Element],
    relationships: Sequence[Relationship],
    stereotype_of: Mapping[UUID, str],
) -> TemplateBody:
    """Turn a diagram into a skeleton: each element becomes a slot of its type.

    Notes, groups and the links between the drawn elements are kept, the links
    by relationship type. No element identity survives — only types.
    """
    ordered_nodes = sorted(scene.nodes, key=lambda n: (n.y, n.x, str(n.element_id)))
    ordered_objects = sorted(scene.objects, key=lambda o: (o.z, o.y, o.x, str(o.id)))
    group_keys = {
        o.id: f"g{i + 1}"
        for i, o in enumerate(o for o in ordered_objects if o.kind is ObjectKind.GROUP)
    }
    slot_keys = {n.element_id: f"s{i + 1}" for i, n in enumerate(ordered_nodes)}
    slots = tuple(
        Slot(
            key=slot_keys[n.element_id],
            element_type=elements[n.element_id].element_type,
            label=elements[n.element_id].name[:100],
            x=n.x,
            y=n.y,
            width=n.width,
            height=n.height,
            stereotype=stereotype_of.get(n.element_id),
            parent_key=group_keys.get(n.parent_object_id) if n.parent_object_id else None,
        )
        for n in ordered_nodes
    )
    groups = tuple(
        TemplateGroup(
            key=group_keys[o.id],
            text=o.text,
            x=o.x,
            y=o.y,
            width=o.width,
            height=o.height,
            colour=o.colour,
            parent_key=group_keys.get(o.parent_id) if o.parent_id else None,
        )
        for o in ordered_objects
        if o.kind is ObjectKind.GROUP
    )
    notes = tuple(
        TemplateNote(
            key=f"n{i + 1}",
            text=o.text,
            x=o.x,
            y=o.y,
            width=o.width,
            height=o.height,
            colour=o.colour,
            parent_key=group_keys.get(o.parent_id) if o.parent_id else None,
            anchor_key=slot_keys.get(o.anchor_element_id) if o.anchor_element_id else None,
        )
        for i, o in enumerate(o for o in ordered_objects if o.kind is ObjectKind.NOTE)
    )
    links = tuple(
        TemplateLink(slot_keys[r.source_id], slot_keys[r.target_id], r.relationship_type)
        for r in relationships
        if r.source_id in slot_keys and r.target_id in slot_keys
    )
    body = TemplateBody(TEMPLATE_VERSION, slots, groups, notes, links)
    check_body(body)
    return body


@dataclass(frozen=True, slots=True)
class Binding:
    """What fills one slot: an existing element, an element to create, or nothing."""

    slot_key: str
    element_id: UUID | None = None
    create_name: str | None = None


@dataclass(frozen=True, slots=True)
class InstantiationPlan:
    bound: tuple[tuple[Slot, UUID], ...]
    to_create: tuple[tuple[Slot, str], ...]
    omitted: tuple[str, ...]


def plan_instantiation(
    body: TemplateBody,
    bindings: Sequence[Binding],
    element_types: Mapping[UUID, ElementType],
    element_stereotypes: Mapping[UUID, frozenset[str]],
) -> InstantiationPlan:
    """Decide what an instantiation does — and refuse every mistake before anything is written."""
    slots = {s.key: s for s in body.slots}
    chosen: dict[str, Binding] = {}
    for binding in bindings:
        slot = slots.get(binding.slot_key)
        if slot is None:
            msg = f"the template has no slot {binding.slot_key!r}"
            raise ValueError(msg)
        if binding.slot_key in chosen:
            msg = f"slot {binding.slot_key!r} is bound twice"
            raise ValueError(msg)
        if binding.element_id is not None and binding.create_name is not None:
            msg = f"slot {binding.slot_key!r}: choose an existing element or create one, not both"
            raise ValueError(msg)
        if binding.create_name is not None and not binding.create_name.strip():
            msg = f"slot {binding.slot_key!r}: the name of the element to create is blank"
            raise ValueError(msg)
        chosen[binding.slot_key] = binding
    bound: list[tuple[Slot, UUID]] = []
    to_create: list[tuple[Slot, str]] = []
    used: set[UUID] = set()
    for key, slot in slots.items():
        wanted = chosen.get(key)
        if wanted is None or (wanted.element_id is None and wanted.create_name is None):
            continue
        binding = wanted
        if binding.create_name is not None:
            if slot.stereotype:
                msg = (
                    f"slot {key!r} wants an element carrying the stereotype {slot.stereotype!r}: "
                    "bind an existing one, a new element would not carry it"
                )
                raise ValueError(msg)
            to_create.append((slot, binding.create_name.strip()))
            continue
        element_id = binding.element_id
        assert element_id is not None
        actual = element_types.get(element_id)
        if actual is not slot.element_type:
            msg = (
                f"slot {key!r} wants a {slot.element_type.value}, "
                f"got {actual.value if actual else 'an unknown element'}"
            )
            raise ValueError(msg)
        if slot.stereotype and slot.stereotype not in element_stereotypes.get(
            element_id, frozenset()
        ):
            msg = f"slot {key!r} wants an element carrying the stereotype {slot.stereotype!r}"
            raise ValueError(msg)
        if element_id in used:
            msg = "an element cannot fill two slots (it would be placed twice)"
            raise ValueError(msg)
        used.add(element_id)
        bound.append((slot, element_id))
    filled = {s.key for s, _ in bound} | {s.key for s, _ in to_create}
    omitted = tuple(key for key in slots if key not in filled)
    return InstantiationPlan(tuple(bound), tuple(to_create), omitted)
