"""A baseline: a frozen copy of the model, comparable and restorable — docs/adr/0045.

Pure. A snapshot holds *values* (the elements, the relationships, the boxes of
diagrams), so a diff is the comparison of two sets and needs no database. What
counts as a modification is the content of an entity — never its timestamps.

A baseline records which package files each element (`package_id`, with its
readable path for a diff), and nothing about the tree of packages itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

from ea.domain.audit import FieldChange, diff_fields
from ea.domain.model import Element, Relationship, reject_nul

MAX_BASELINE_NAME_LENGTH: Final = 200
MAX_BASELINE_DESCRIPTION_LENGTH: Final = 2000
#: What a capture accepts to copy. Beyond either, a baseline would be a second
#: database: refused before anything is written (`BaselineTooLargeError`).
MAX_BASELINE_ELEMENTS: Final = 20_000
MAX_BASELINE_BYTES: Final = 64 * 1024 * 1024
#: How a diff names an element that sits outside every package.
ROOT_LABEL: Final = "(root)"


@dataclass(frozen=True, slots=True)
class BaselineLimits:
    elements: int = MAX_BASELINE_ELEMENTS
    bytes: int = MAX_BASELINE_BYTES


@dataclass(frozen=True, slots=True)
class Baseline:
    """A named snapshot. The counts are read from the copy, never written."""

    id: UUID
    name: str
    description: str
    created_by_sub: str
    created_by: str
    created_at: datetime
    element_count: int = 0
    relationship_count: int = 0
    box_count: int = 0

    @classmethod
    def create(
        cls, *, name: str, description: str, created_by_sub: str, created_by: str, now: datetime
    ) -> Baseline:
        cleaned = reject_nul(name, "a baseline name").strip()
        if not cleaned:
            msg = "a baseline name cannot be blank"
            raise ValueError(msg)
        if len(cleaned) > MAX_BASELINE_NAME_LENGTH:
            msg = f"a baseline name is at most {MAX_BASELINE_NAME_LENGTH} characters"
            raise ValueError(msg)
        note = reject_nul(description, "a baseline description").strip()
        if len(note) > MAX_BASELINE_DESCRIPTION_LENGTH:
            msg = f"a baseline description is at most {MAX_BASELINE_DESCRIPTION_LENGTH} characters"
            raise ValueError(msg)
        return cls(
            id=uuid4(),
            name=cleaned,
            description=note,
            created_by_sub=created_by_sub,
            created_by=created_by,
            created_at=now,
        )

    def with_counts(self, elements: int, relationships: int, boxes: int) -> Baseline:
        return Baseline(
            self.id,
            self.name,
            self.description,
            self.created_by_sub,
            self.created_by,
            self.created_at,
            elements,
            relationships,
            boxes,
        )


def baseline_view(baseline: Baseline) -> dict[str, Any]:
    return {
        "id": str(baseline.id),
        "name": baseline.name,
        "description": baseline.description,
        "elements": baseline.element_count,
        "relationships": baseline.relationship_count,
        "boxes": baseline.box_count,
    }


@dataclass(frozen=True, slots=True)
class BoxSnapshot:
    """One box of one diagram, as captured."""

    diagram_id: UUID
    element_id: UUID
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    """The model as values. `package_paths` reads a package id as `A / B`, for a diff."""

    elements: tuple[Element, ...]
    relationships: tuple[Relationship, ...]
    boxes: tuple[BoxSnapshot, ...] = ()
    package_paths: Mapping[UUID, str] = field(default_factory=dict)


def element_content(element: Element) -> dict[str, Any]:
    """What makes two versions of an element different — not its timestamps."""
    return {
        "element_type": element.element_type.value,
        "name": element.name,
        "description": element.description,
        "documentation": element.documentation,
        "package_id": str(element.package_id) if element.package_id else None,
        "properties": dict(element.properties),
    }


def relationship_content(relationship: Relationship) -> dict[str, Any]:
    return {
        "relationship_type": relationship.relationship_type.value,
        "source_id": str(relationship.source_id),
        "target_id": str(relationship.target_id),
        "name": relationship.name,
        "access_type": relationship.access_type.value if relationship.access_type else None,
        "directed": relationship.directed,
        "properties": dict(relationship.properties),
    }


@dataclass(frozen=True, slots=True)
class EntityRef:
    kind: str  # "element" | "relationship"
    id: UUID
    label: str
    type: str


@dataclass(frozen=True, slots=True)
class Modified:
    kind: str
    id: UUID
    label: str
    type: str
    changes: tuple[FieldChange, ...]


@dataclass(frozen=True, slots=True)
class BaselineDiff:
    elements_added: tuple[EntityRef, ...]
    elements_removed: tuple[EntityRef, ...]
    elements_modified: tuple[Modified, ...]
    relationships_added: tuple[EntityRef, ...]
    relationships_removed: tuple[EntityRef, ...]
    relationships_modified: tuple[Modified, ...]

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.elements_added,
                self.elements_removed,
                self.elements_modified,
                self.relationships_added,
                self.relationships_removed,
                self.relationships_modified,
            )
        )


def _link_label(link: Relationship, names: dict[UUID, str]) -> str:
    source = names.get(link.source_id, str(link.source_id))
    target = names.get(link.target_id, str(link.target_id))
    return f"{source} —{link.relationship_type.value}→ {target}"


def _package_label(snapshot: ModelSnapshot, package_id: str | None) -> str:
    if package_id is None:
        return ROOT_LABEL
    return snapshot.package_paths.get(UUID(package_id), package_id)


def _element_changes(
    older: ModelSnapshot, newer: ModelSnapshot, before: Element, after: Element
) -> tuple[FieldChange, ...]:
    """Field by field; a package is compared by id and *shown* by path.

    Comparing paths would make renaming a package look like a change to every
    element in it.
    """
    changes = []
    for change in diff_fields(element_content(before), element_content(after)):
        if change.field == "package_id":
            change = FieldChange(
                "package",
                _package_label(older, change.before),
                _package_label(newer, change.after),
            )
        changes.append(change)
    return tuple(sorted(changes, key=lambda c: c.field))


def _by_label(refs: Sequence[EntityRef]) -> tuple[EntityRef, ...]:
    return tuple(sorted(refs, key=lambda ref: (ref.label, str(ref.id))))


def _by_modified_label(items: Sequence[Modified]) -> tuple[Modified, ...]:
    return tuple(sorted(items, key=lambda m: (m.label, str(m.id))))


def diff_snapshots(older: ModelSnapshot, newer: ModelSnapshot) -> BaselineDiff:
    """`added` is in `newer` only, `removed` in `older` only, `modified` in both and different.

    Deterministic: every list is sorted by label then id, whatever the order of
    the input.
    """
    names = {e.id: e.name for e in (*older.elements, *newer.elements)}
    old_el = {e.id: e for e in older.elements}
    new_el = {e.id: e for e in newer.elements}
    old_rel = {r.id: r for r in older.relationships}
    new_rel = {r.id: r for r in newer.relationships}

    def element_ref(element: Element) -> EntityRef:
        return EntityRef("element", element.id, element.name, element.element_type.value)

    def link_ref(link: Relationship) -> EntityRef:
        return EntityRef(
            "relationship", link.id, _link_label(link, names), link.relationship_type.value
        )

    elements_modified = []
    for identifier in old_el.keys() & new_el.keys():
        after = new_el[identifier]
        changes = _element_changes(older, newer, old_el[identifier], after)
        if changes:
            elements_modified.append(
                Modified("element", identifier, after.name, after.element_type.value, changes)
            )
    relationships_modified = []
    for identifier in old_rel.keys() & new_rel.keys():
        after_link = new_rel[identifier]
        changes = diff_fields(
            relationship_content(old_rel[identifier]), relationship_content(after_link)
        )
        if changes:
            relationships_modified.append(
                Modified(
                    "relationship",
                    identifier,
                    _link_label(after_link, names),
                    after_link.relationship_type.value,
                    changes,
                )
            )

    return BaselineDiff(
        elements_added=_by_label([element_ref(new_el[i]) for i in new_el.keys() - old_el.keys()]),
        elements_removed=_by_label([element_ref(old_el[i]) for i in old_el.keys() - new_el.keys()]),
        elements_modified=_by_modified_label(elements_modified),
        relationships_added=_by_label(
            [link_ref(new_rel[i]) for i in new_rel.keys() - old_rel.keys()]
        ),
        relationships_removed=_by_label(
            [link_ref(old_rel[i]) for i in old_rel.keys() - new_rel.keys()]
        ),
        relationships_modified=_by_modified_label(relationships_modified),
    )
