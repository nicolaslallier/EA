"""A baseline: a frozen copy of the model, comparable and restorable — docs/adr/0047.

Pure. A snapshot holds *values* (the elements, the relationships, the boxes of
diagrams), so a diff is the comparison of two sets and needs no database. What
counts as a modification is the content of an entity — never its timestamps.

A baseline records which package files each element (`package_id`, with its
readable path for a diff), and nothing about the tree of packages itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import islice
from typing import Any, Final
from uuid import UUID, uuid4

from ea.domain.approval import reviewed_after_change
from ea.domain.archimate import validate_relationship
from ea.domain.audit import AUDIT_LISTED_IDS, FieldChange, diff_fields
from ea.domain.errors import BaselineTooLargeError, IllegalRelationshipError
from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, read_vrf
from ea.domain.model import Element, Relationship, reject_nul
from ea.domain.package_versions import Freezes, FrozenBy

MAX_BASELINE_NAME_LENGTH: Final = 200
MAX_BASELINE_DESCRIPTION_LENGTH: Final = 2000
#: What a baseline accepts to copy or to read back whole, per kind of row. Beyond
#: any of them a baseline would be a second database: refused with a count,
#: before a row is loaded (`BaselineTooLargeError`). Declared once; the store and
#: every path that reads a baseline or the live model import them.
MAX_BASELINE_ELEMENTS: Final = 20_000
MAX_BASELINE_RELATIONSHIPS: Final = 100_000
MAX_BASELINE_BOXES: Final = 100_000
MAX_BASELINE_DIAGRAMS: Final = 5_000
MAX_BASELINE_BYTES: Final = 64 * 1024 * 1024
#: How a diff names an element that sits outside every package.
ROOT_LABEL: Final = "(root)"


@dataclass(frozen=True, slots=True)
class BaselineLimits:
    elements: int = MAX_BASELINE_ELEMENTS
    relationships: int = MAX_BASELINE_RELATIONSHIPS
    boxes: int = MAX_BASELINE_BOXES
    diagrams: int = MAX_BASELINE_DIAGRAMS
    bytes: int = MAX_BASELINE_BYTES


@dataclass(frozen=True, slots=True)
class ModelSize:
    """What a model (or a baseline) weighs, counted by the server before anything is loaded."""

    elements: int = 0
    relationships: int = 0
    boxes: int = 0
    diagrams: int = 0
    #: Text of the rows plus a fixed overhead per row, so many small rows count too.
    bytes: int = 0


def refuse_oversize(size: ModelSize, limits: BaselineLimits, *, what: str = "the model") -> None:
    """Raise `BaselineTooLargeError` for the first kind of row past its limit."""
    for kind in ("elements", "relationships", "boxes", "diagrams", "bytes"):
        held, allowed = getattr(size, kind), getattr(limits, kind)
        if held > allowed:
            msg = f"{what} holds {held} {kind}; a baseline handles at most {allowed}"
            raise BaselineTooLargeError(msg)


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
    #: Set when the baseline copies one package's subtree rather than the whole model.
    scope_package_id: UUID | None = None
    scope_path: str | None = None

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
        return replace(
            self, element_count=elements, relationship_count=relationships, box_count=boxes
        )


def baseline_view(baseline: Baseline) -> dict[str, Any]:
    return {
        "id": str(baseline.id),
        "name": baseline.name,
        "description": baseline.description,
        "elements": baseline.element_count,
        "relationships": baseline.relationship_count,
        "boxes": baseline.box_count,
        "scope_package_id": str(baseline.scope_package_id) if baseline.scope_package_id else None,
        "scope_path": baseline.scope_path,
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


def restricted_to(
    current: ModelSnapshot,
    *,
    packages: frozenset[UUID],
    diagrams: frozenset[UUID],
    known: frozenset[UUID],
) -> ModelSnapshot:
    """The part of `current` a subtree baseline is compared with (docs/adr/0074).

    The elements filed in `packages` *or* that the baseline knows (`known`: one moved out
    since is modified, not removed), the links with both ends kept, the boxes of `diagrams`.
    """
    elements = tuple(e for e in current.elements if e.package_id in packages or e.id in known)
    ids = {e.id for e in elements}
    return replace(
        current,
        elements=elements,
        relationships=tuple(
            r for r in current.relationships if r.source_id in ids and r.target_id in ids
        ),
        boxes=tuple(b for b in current.boxes if b.diagram_id in diagrams),
    )


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


@dataclass(frozen=True, slots=True)
class ElementUpdate:
    """An element to put back: what it is now, what it becomes, and the fields between."""

    current: Element
    restored: Element
    changes: tuple[FieldChange, ...]


@dataclass(frozen=True, slots=True)
class Cascade:
    """What deleting one element takes along, as `delete_element` journals it: exact
    counts, and the first `AUDIT_LISTED_IDS` of each kind. Documents are not part of a
    baseline, so the repository fills them in, in the transaction."""

    element_id: UUID
    relationship_count: int = 0
    relationships: tuple[tuple[UUID, str], ...] = ()  # (id, relationship type)
    document_count: int = 0
    document_ids: tuple[UUID, ...] = ()
    box_count: int = 0
    diagram_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """What a restoration would do, and everything it costs.

    `documents_lost` is the one figure `plan_restore` cannot know (documents are
    not part of a baseline): the repository counts them, in the transaction.
    The lists are sorted, so the same two models always give the same plan —
    which is what lets the transaction check that the plan it applies is the
    plan that was read.
    """

    elements_to_create: tuple[Element, ...] = ()
    elements_to_update: tuple[ElementUpdate, ...] = ()
    elements_to_delete: tuple[Element, ...] = ()
    relationships_to_create: tuple[Relationship, ...] = ()
    relationships_to_delete: tuple[Relationship, ...] = ()
    cascaded_relationships: int = 0
    boxes_to_restore: tuple[BoxSnapshot, ...] = ()
    #: The boxes `boxes_to_restore` overwrites, as they are now.
    boxes_overwritten: tuple[BoxSnapshot, ...] = ()
    #: One per deleted element, in the order of `elements_to_delete`.
    cascades: tuple[Cascade, ...] = ()
    documents_lost: int = 0
    boxes_lost: int = 0
    warnings: tuple[str, ...] = ()
    blocked: tuple[Element, ...] = ()
    #: For a scoped baseline: the elements absent from it, filed in its subtree now, that
    #: existed before it was captured — moved in since, not created since. Never deleted.
    kept_moved_in: tuple[Element, ...] = ()
    #: Every package a write of this plan lands in: where an element is and where it goes
    #: back, both ends of a link it writes or cascades away (the repository adds the
    #: packages of the diagrams it writes). What the freeze prediction is checked against.
    packages_written: frozenset[UUID] = frozenset()
    #: The publication that freezes a package this plan writes, for a scoped baseline. Left
    #: out of `plan_digest`: a frozen plan is never applied, and the freeze is checked first.
    frozen_by: FrozenBy | None = None

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked) or self.frozen_by is not None

    @property
    def is_noop(self) -> bool:
        return not any(
            (
                self.elements_to_create,
                self.elements_to_update,
                self.elements_to_delete,
                self.relationships_to_create,
                self.relationships_to_delete,
                self.boxes_to_restore,
            )
        )


def plan_digest(plan: RestorePlan) -> str:
    """A fingerprint of everything the plan would do and cost.

    A dry run hands it out; applying hands it back (`expected_digest`), and the
    service refuses to apply a plan other than the one that was read. Timestamps
    are left out, like in a diff: only content decides.
    """

    def element(e: Element) -> list[Any]:
        return [str(e.id), element_content(e)]

    def link(r: Relationship) -> list[Any]:
        return [str(r.id), relationship_content(r)]

    def box(b: BoxSnapshot) -> list[Any]:
        return [str(b.diagram_id), str(b.element_id), b.x, b.y, b.width, b.height]

    material = {
        "create": [element(e) for e in plan.elements_to_create],
        "update": [
            [str(u.current.id), element_content(u.current), element_content(u.restored)]
            for u in plan.elements_to_update
        ],
        "delete": [element(e) for e in plan.elements_to_delete],
        "links_create": [link(r) for r in plan.relationships_to_create],
        "links_delete": [link(r) for r in plan.relationships_to_delete],
        "cascaded": plan.cascaded_relationships,
        "boxes": [box(b) for b in plan.boxes_to_restore],
        # The positions about to be overwritten: a box moved since the plan was read.
        "boxes_overwritten": sorted(box(b) for b in plan.boxes_overwritten),
        # What each deletion takes along, as the journal will list it.
        "cascades": sorted(
            [
                str(c.element_id),
                c.relationship_count,
                sorted([str(i), kind] for i, kind in c.relationships),
                c.document_count,
                sorted(str(i) for i in c.document_ids),
                c.box_count,
                sorted(str(i) for i in c.diagram_ids),
            ]
            for c in plan.cascades
        ),
        "documents_lost": plan.documents_lost,
        "boxes_lost": plan.boxes_lost,
        "warnings": list(plan.warnings),
        "blocked": [str(e.id) for e in plan.blocked],
        "kept_moved_in": [str(e.id) for e in plan.kept_moved_in],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def freeze_of(scope: UUID, plan: RestorePlan, freezes: Freezes) -> FrozenBy | None:
    """The publication that blocks restoring a subtree: its own, else the first (by path,
    then id) that freezes a package the plan writes — a nested one, or one outside."""
    if scope in freezes:
        return freezes[scope]
    hits = [freezes[p] for p in plan.packages_written if p in freezes]
    return min(hits, key=lambda f: (f.path, str(f.package_id)), default=None)


def _element_order(element: Element) -> tuple[str, str]:
    return (element.name, str(element.id))


def _link_order(link: Relationship) -> tuple[str, str]:
    return (link.relationship_type.value, str(link.id))


#: How many blocked elements a log line or an error names, and how much of each name.
#: The cap is applied at the source (`islice`), before anything is formatted: the
#: exact count always travels beside.
LOGGED_BLOCKED: Final = 20
LOGGED_NAME_LENGTH: Final = 80


def _shown(name: str) -> str:
    return name if len(name) <= LOGGED_NAME_LENGTH else name[:LOGGED_NAME_LENGTH] + "..."


def refusal_context(baseline_id: UUID, blocked: Sequence[Element] = ()) -> dict[str, Any]:
    """The structured fields of a refused restoration's log line — bounded, never a full list."""
    return {
        "baseline_id": str(baseline_id),
        "blocked_count": len(blocked),
        "blocked": [
            {"id": str(e.id), "name": _shown(e.name)} for e in islice(blocked, LOGGED_BLOCKED)
        ],
    }


def blocked_message(blocked: Sequence[Element]) -> str:
    """`'a', 'b' and 4998 more`: the first few names, and the exact count of the rest."""
    names = ", ".join(repr(_shown(e.name)) for e in islice(blocked, LOGGED_BLOCKED))
    more = len(blocked) - LOGGED_BLOCKED
    tail = f" and {more} more" if more > 0 else ""
    return f"{len(blocked)} protected element(s) would be touched: {names}{tail}"


def _cascades_of(
    deleted: Sequence[Element], links: Iterable[Relationship], boxes: Iterable[BoxSnapshot]
) -> tuple[Cascade, ...]:
    """What each deleted element takes along — from indexes built once, never a scan per element."""
    wanted = {e.id for e in deleted}
    incident: dict[UUID, list[Relationship]] = {i: [] for i in wanted}
    for link in links:
        for end in {link.source_id, link.target_id} & wanted:
            incident[end].append(link)
    on_diagrams: dict[UUID, list[UUID]] = {i: [] for i in wanted}
    for box in boxes:
        if box.element_id in wanted:
            on_diagrams[box.element_id].append(box.diagram_id)
    cascades = []
    for element in deleted:
        touching = sorted(incident[element.id], key=lambda link: (link.created_at, str(link.id)))
        diagrams = sorted(on_diagrams[element.id])
        cascades.append(
            Cascade(
                element_id=element.id,
                relationship_count=len(touching),
                relationships=tuple(
                    (link.id, link.relationship_type.value) for link in touching[:AUDIT_LISTED_IDS]
                ),
                box_count=len(diagrams),
                diagram_ids=tuple(diagrams[:AUDIT_LISTED_IDS]),
            )
        )
    return tuple(cascades)


def _claims(element: Element) -> set[tuple[str, ...]]:
    """What an element holds that is unique: its (type, name), its address, its prefix."""
    vrf = read_vrf(element.properties)
    claims: set[tuple[str, ...]] = {("name", element.element_type.value, element.name)}
    for key, kind in ((ADDRESS_PROPERTY, "address"), (PREFIX_PROPERTY, "prefix")):
        if element.properties.get(key):
            claims.add((kind, vrf, element.properties[key]))
    return claims


def _taken_by(written: Sequence[Element], protected: Sequence[Element]) -> set[UUID]:
    """The protected elements whose unique claims one of the `written` elements would take."""
    held = {claim: element.id for element in protected for claim in _claims(element)}
    return {
        held[claim]
        for element in written
        for claim in _claims(element)
        if claim in held and held[claim] != element.id
    }


def plan_restore(
    snapshot: ModelSnapshot,
    current: ModelSnapshot,
    *,
    keep_new: bool,
    packages_present: frozenset[UUID],
    protected_ids: frozenset[UUID] = frozenset(),
    diagrams_present: frozenset[UUID] = frozenset(),
    deletable: frozenset[UUID] | None = None,
    captured_at: datetime | None = None,
) -> RestorePlan:
    """Make `current` equal `snapshot` — over the elements the snapshot knows.

    `keep_new` keeps what was created since the baseline (and every link that
    touches it); otherwise it is deleted, and its links go with it by the
    foreign keys — counted in `cascaded_relationships`, not listed. A link the
    metamodel no longer allows is a warning and is skipped.

    **A protected element is touched by nothing.** `protected_ids` (the approved
    and locked elements, docs/adr/0048) blocks the plan whenever the restoration
    would: change its fields
    or its package; delete it (and so its documents, boxes and links); delete,
    replace or cascade away a link it holds; or write an element that would take
    its (type, name), its address or its prefix. The caller re-reads the set
    inside the transaction that applies the plan.

    `deletable`, for a subtree baseline, is what the plan may delete: the elements filed
    in that subtree now. Everything else stays, while the plan still sees the whole model,
    so a deletion cascading to a protected element outside is blocked like any other.
    `captured_at`, the instant that baseline was taken, spares an element created before it:
    absent from a subtree's copy, it was moved in since, not created since — deleting it
    would take an element (its documents, links and boxes) the baseline never meant to
    undo. It is kept and listed in `kept_moved_in` (docs/adr/0077).

    `packages_present` is the set of packages that exist *now*: an element whose
    package is gone is restored at the root, and the plan says so — a package is
    never recreated, and a foreign key is never left to fail.
    """
    warnings: list[str] = []

    def placed(element: Element) -> Element:
        gone = element.package_id is not None and element.package_id not in packages_present
        return replace(element, package_id=None) if gone else element

    snap_el = {e.id: placed(e) for e in sorted(snapshot.elements, key=_element_order)}
    cur_el = {e.id: e for e in current.elements}

    to_create = tuple(
        sorted((snap_el[i] for i in snap_el.keys() - cur_el.keys()), key=_element_order)
    )
    updates = []
    for identifier in snap_el.keys() & cur_el.keys():
        changes = diff_fields(
            element_content(cur_el[identifier]), element_content(snap_el[identifier])
        )
        if changes:
            now_held = cur_el[identifier]
            # A baseline does not copy the review state (docs/adr/0048): what is
            # written keeps the state the element has now — and is never left
            # approved, for a content it was not approved on.
            restored = reviewed_after_change(
                replace(
                    snap_el[identifier],
                    approval_state=now_held.approval_state,
                    locked=now_held.locked,
                    approved_by_sub=now_held.approved_by_sub,
                    approved_at=now_held.approved_at,
                    revision=now_held.revision,
                )
            )
            updates.append(ElementUpdate(now_held, restored, changes))
    updates.sort(key=lambda u: _element_order(u.restored))
    new_ids = cur_el.keys() - snap_el.keys()
    kept_moved_in: tuple[Element, ...] = ()
    if deletable is not None:
        new_ids &= deletable
        if captured_at is not None and not keep_new:
            older = {i for i in new_ids if cur_el[i].created_at < captured_at}
            new_ids -= older
            kept_moved_in = tuple(sorted((cur_el[i] for i in older), key=_element_order))
    to_delete = () if keep_new else tuple(sorted((cur_el[i] for i in new_ids), key=_element_order))
    deleted_ids = {e.id for e in to_delete}

    snap_rel = {r.id: r for r in snapshot.relationships}
    cur_rel = {r.id: r for r in current.relationships}
    perimeter = snap_el.keys()
    links_to_delete = [
        link
        for identifier, link in cur_rel.items()
        if (
            identifier not in snap_rel
            and link.source_id in perimeter
            and link.target_id in perimeter
        )
        or (
            identifier in snap_rel
            and relationship_content(link) != relationship_content(snap_rel[identifier])
        )
    ]
    deleting = {link.id for link in links_to_delete}
    wanted = [
        link
        for identifier, link in snap_rel.items()
        if identifier not in cur_rel or identifier in deleting
    ]
    links_to_create: list[Relationship] = []
    for wanted_link in sorted(wanted, key=_link_order):
        try:
            validate_relationship(
                wanted_link.relationship_type,
                wanted_link.source_type,
                wanted_link.target_type,
                same_element=wanted_link.source_id == wanted_link.target_id,
            )
        except IllegalRelationshipError as refusal:
            warnings.append(f"relationship {wanted_link.id} skipped: {refusal}")
        else:
            links_to_create.append(wanted_link)
    cascaded = sum(
        1
        for identifier, link in cur_rel.items()
        if identifier not in deleting
        and (link.source_id in deleted_ids or link.target_id in deleted_ids)
    )

    present_after = set(snap_el) | (set(cur_el) if keep_new else set())
    current_boxes = {(b.diagram_id, b.element_id): b for b in current.boxes}
    boxes = tuple(
        sorted(
            (
                box
                for box in snapshot.boxes
                if box.diagram_id in diagrams_present
                and box.element_id in present_after
                and current_boxes.get((box.diagram_id, box.element_id)) != box
            ),
            key=lambda b: (str(b.diagram_id), str(b.element_id)),
        )
    )
    boxes_lost = sum(1 for box in current.boxes if box.element_id in deleted_ids)
    overwritten = tuple(
        current_boxes[(b.diagram_id, b.element_id)]
        for b in boxes
        if (b.diagram_id, b.element_id) in current_boxes
    )
    cascades = _cascades_of(to_delete, cur_rel.values(), current.boxes)

    # Only for what the plan really writes: an element already at the root is not news.
    asked_for = {e.id: e.package_id for e in snapshot.elements}
    for written in (*to_create, *(u.restored for u in updates)):
        asked = asked_for[written.id]
        if asked is not None and asked not in packages_present:
            path = snapshot.package_paths.get(asked, str(asked))
            warnings.append(
                f"element {written.name!r}: its package {path!r} no longer exists, "
                "it is restored at the root"
            )
    if kept_moved_in:
        warnings.append(
            f"{len(kept_moved_in)} element(s) moved into the subtree since the capture are "
            "kept: they existed before it, and a restore deletes only what was created since"
        )
    if to_create:
        warnings.append(
            f"{len(to_create)} element(s) come back without their documents: "
            "documents are not part of a baseline"
        )

    cascaded_links = [
        link
        for identifier, link in cur_rel.items()
        if identifier not in deleting
        and (link.source_id in deleted_ids or link.target_id in deleted_ids)
    ]
    to_write = [*to_create, *(u.restored for u in updates)]
    touched = (
        {u.current.id for u in updates}
        | deleted_ids
        | {
            end
            for link in (*links_to_delete, *links_to_create, *cascaded_links)
            for end in (link.source_id, link.target_id)
        }
        | {box.element_id for box in boxes}
        | _taken_by(to_write, [cur_el[i] for i in protected_ids if i in cur_el])
    )
    blocked = tuple(
        sorted(
            (cur_el[i] for i in touched if i in protected_ids and i in cur_el), key=_element_order
        )
    )
    # What the writes reach: the elements written (updated, deleted, or `touch`ed as the end
    # of a link that appears or goes), by where they are now and where they go back to.
    reached = (
        {u.current.id for u in updates}
        | deleted_ids
        | {
            end
            for link in (*links_to_delete, *links_to_create, *cascaded_links)
            for end in (link.source_id, link.target_id)
        }
    )
    packages_written = frozenset(
        package
        for package in (
            *(cur_el[i].package_id for i in reached if i in cur_el),
            *(e.package_id for e in to_write),
        )
        if package is not None
    )

    return RestorePlan(
        elements_to_create=to_create,
        elements_to_update=tuple(updates),
        elements_to_delete=to_delete,
        relationships_to_create=tuple(links_to_create),
        relationships_to_delete=tuple(sorted(links_to_delete, key=_link_order)),
        cascaded_relationships=cascaded,
        boxes_to_restore=boxes,
        boxes_overwritten=overwritten,
        cascades=cascades,
        boxes_lost=boxes_lost,
        warnings=tuple(warnings),
        blocked=blocked,
        kept_moved_in=kept_moved_in,
        packages_written=packages_written,
    )
