"""What the catalogue remembers about a change — see docs/adr/0046.

Pure: no framework, no driver. An `AuditEvent` is one *use case*, not one SQL
row: deleting an element is one event whose `before` lists what the foreign
keys took with it. `services/` builds the events, the repositories insert them
in the transaction of the write itself.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from ea.domain.errors import InvalidCursorError
from ea.domain.external import ExternalReference
from ea.domain.generated_views import QuerySource
from ea.domain.model import Element, Relationship
from ea.domain.query import to_json

if TYPE_CHECKING:
    # Only for annotations: `diagrams` imports `ports`, which imports this module.
    from ea.domain.diagram_objects import DiagramScene
    from ea.domain.diagram_templates import DiagramTemplate
    from ea.domain.diagrams import Diagram
    from ea.domain.documents import Document
    from ea.domain.model import StereotypeRef
    from ea.domain.packages import Package
    from ea.domain.profiles import Profile
    from ea.domain.saved_queries import SavedQuery

MAX_AUDIT_PAGE: Final = 200
DEFAULT_AUDIT_PAGE: Final = 50
#: How many ids an event lists for one kind of thing it moved or took along; the
#: count beside them is always exact, so a truncated list says it is truncated.
AUDIT_LISTED_IDS: Final = 100


class EntityKind(StrEnum):
    ELEMENT = "element"
    RELATIONSHIP = "relationship"
    PACKAGE = "package"
    SAVED_QUERY = "saved_query"
    DIAGRAM = "diagram"
    DOCUMENT = "document"
    BASELINE = "baseline"
    EXTERNAL_REFERENCE = "external_reference"
    PROFILE = "profile"
    TEMPLATE = "template"


class AuditChannel(StrEnum):
    """Which door the caller came through — said by the adapter, never by the caller."""

    HTTP = "http"
    MCP = "mcp"
    SYSTEM = "system"


class AuditAction(StrEnum):
    """The closed list of things the trail can say. Never a free string."""

    ELEMENT_CREATED = "element.created"
    ELEMENT_UPDATED = "element.updated"
    ELEMENT_DELETED = "element.deleted"
    ELEMENT_SUBMITTED = "element.submitted"
    ELEMENT_APPROVED = "element.approved"
    ELEMENT_REJECTED = "element.rejected"
    ELEMENT_REOPENED = "element.reopened"
    ELEMENT_LOCKED = "element.locked"
    ELEMENT_UNLOCKED = "element.unlocked"
    ELEMENT_STEREOTYPES_CHANGED = "element.stereotypes_changed"
    PROFILE_CREATED = "profile.created"
    PROFILE_UPDATED = "profile.updated"
    PROFILE_DELETED = "profile.deleted"
    TEMPLATE_CREATED = "template.created"
    TEMPLATE_UPDATED = "template.updated"
    TEMPLATE_DELETED = "template.deleted"
    RELATIONSHIP_CREATED = "relationship.created"
    RELATIONSHIP_UPDATED = "relationship.updated"
    RELATIONSHIP_DELETED = "relationship.deleted"
    DIAGRAM_CREATED = "diagram.created"
    DIAGRAM_UPDATED = "diagram.updated"
    DIAGRAM_DELETED = "diagram.deleted"
    DIAGRAM_LAYOUT_REPLACED = "diagram.layout_replaced"
    DIAGRAM_GENERATED = "diagram.generated"
    DIAGRAM_FROZEN = "diagram.frozen"
    DIAGRAM_UNFROZEN = "diagram.unfrozen"
    DOCUMENT_ATTACHED = "document.attached"
    DOCUMENT_REVISED = "document.revised"
    DOCUMENT_DISCARDED = "document.discarded"
    PACKAGE_CREATED = "package.created"
    PACKAGE_UPDATED = "package.updated"
    PACKAGE_DELETED = "package.deleted"
    PACKAGE_ELEMENTS_MOVED = "package.elements_moved"
    SAVED_QUERY_CREATED = "saved_query.created"
    SAVED_QUERY_UPDATED = "saved_query.updated"
    SAVED_QUERY_DELETED = "saved_query.deleted"
    BASELINE_CREATED = "baseline.created"
    BASELINE_DELETED = "baseline.deleted"
    BASELINE_RESTORED = "baseline.restored"
    EXTERNAL_REFERENCE_LINKED = "external_reference.linked"
    EXTERNAL_REFERENCE_UNLINKED = "external_reference.unlinked"
    EXTERNAL_REFERENCE_REFRESHED = "external_reference.refreshed"

    @property
    def kind(self) -> EntityKind:
        return EntityKind(self.value.split(".", 1)[0])


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    before: Any
    after: Any


def _flatten(value: Mapping[str, Any] | None, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, item in (value or {}).items():
        path = f"{prefix}{key}"
        if isinstance(item, Mapping):
            # An empty map has no entry of its own: `{}` -> `{"owner": ...}` is one
            # change, `properties.owner`, not two.
            flat.update(_flatten(item, f"{path}."))
        else:
            flat[path] = item
    return flat


def diff_fields(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    *,
    ignore: frozenset[str] = frozenset(),
) -> tuple[FieldChange, ...]:
    """The fields that differ, sorted by name; nested maps are walked (`properties.owner`)."""
    old, new = _flatten(before), _flatten(after)
    return tuple(
        FieldChange(field, old.get(field), new.get(field))
        for field in sorted(old.keys() | new.keys())
        if field not in ignore and old.get(field) != new.get(field)
    )


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: UUID
    at: datetime
    actor_sub: str
    actor_name: str
    action: AuditAction
    entity_kind: EntityKind
    entity_id: UUID | None
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    request_id: str
    channel: AuditChannel
    correlation: str | None = None

    @property
    def changes(self) -> tuple[FieldChange, ...]:
        # `updated_at` moves on every write: it is noise in a diff.
        return diff_fields(self.before, self.after, ignore=frozenset({"updated_at"}))


#: What a repository write takes as `audit`: the events themselves (a creation
#: has no "before" to read), or a function of the row the write replaces. The
#: second form is the one that matters: the repository reads that row inside
#: the transaction of the write, under its lock, and asks the function for the
#: events then — so the `before` of an event is the row really replaced, and
#: two concurrent writers cannot both log the same one.
type AuditOf[Before] = Sequence[AuditEvent] | Callable[[Before], Sequence[AuditEvent]]


def events_for[Before](audit: AuditOf[Before], before: Before) -> Sequence[AuditEvent]:
    """The events of a write, given the row it replaces (read in its transaction)."""
    return audit if isinstance(audit, Sequence) else audit(before)


# --- Read representations: what `before` and `after` hold ------------------


def element_view(element: Element) -> dict[str, Any]:
    return {
        "id": str(element.id),
        "element_type": element.element_type.value,
        "layer": element.layer,
        "aspect": element.element_type.aspect.value,
        "name": element.name,
        "description": element.description,
        "documentation": element.documentation,
        "package_id": str(element.package_id) if element.package_id else None,
        "properties": dict(element.properties),
        "approval_state": element.approval_state.value,
        "locked": element.locked,
        "approved_by_sub": element.approved_by_sub,
        "approved_at": element.approved_at.isoformat() if element.approved_at else None,
        "created_at": element.created_at.isoformat(),
        "updated_at": element.updated_at.isoformat(),
    }


def relationship_view(relationship: Relationship) -> dict[str, Any]:
    return {
        "id": str(relationship.id),
        "relationship_type": relationship.relationship_type.value,
        "source_id": str(relationship.source_id),
        "target_id": str(relationship.target_id),
        "source_type": relationship.source_type.value,
        "target_type": relationship.target_type.value,
        "name": relationship.name,
        "access_type": relationship.access_type.value if relationship.access_type else None,
        "directed": relationship.directed,
        "properties": dict(relationship.properties),
        "created_at": relationship.created_at.isoformat(),
    }


def diagram_view(diagram: Diagram) -> dict[str, Any]:
    return {
        "id": str(diagram.id),
        "name": diagram.name,
        "description": diagram.description,
        "package_id": str(diagram.package_id) if diagram.package_id else None,
        "generation": diagram.generation.value if diagram.generation else None,
        "source": _source_view(diagram),
    }


def _source_view(diagram: Diagram) -> dict[str, Any] | None:
    """Where a generated view reads: ids and a flag, never a name."""
    source = diagram.source
    if source is None:
        return None
    if isinstance(source, QuerySource):
        return {"query_id": str(source.query_id)}
    return {"package_id": str(source.package_id), "recursive": source.recursive}


def scene_views(
    before: DiagramScene, after: DiagramScene, *, revision: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Counts of what a saved scene changed - never a coordinate, never the text of a note.

    One event per save, not one per box (an autosave fires at every pause); the
    figures say how much moved, the diagram's own revision says which save it was.
    """
    kept = {node.element_id: node for node in before.nodes}
    moved = sum(
        1
        for node in after.nodes
        if node.element_id in kept
        and (node.x, node.y, node.width, node.height)
        != (
            kept[node.element_id].x,
            kept[node.element_id].y,
            kept[node.element_id].width,
            kept[node.element_id].height,
        )
    )
    old_ids = {node.element_id for node in before.nodes}
    new_ids = {node.element_id for node in after.nodes}
    return (
        {
            "diagram_revision": revision,
            "nodes": len(before.nodes),
            "objects": len(before.objects),
            "edges": len(before.edges),
        },
        {
            "diagram_revision": revision + 1,
            "nodes": len(after.nodes),
            "objects": len(after.objects),
            "edges": len(after.edges),
            "nodes_added": len(new_ids - old_ids),
            "nodes_removed": len(old_ids - new_ids),
            "nodes_moved": moved,
        },
    )


def profile_view(profile: Profile) -> dict[str, Any]:
    """A profile and the shape of its stereotypes - reference data, no element and no value."""
    return {
        "id": str(profile.id),
        "name": profile.name,
        "description": profile.description,
        "stereotypes": [
            {
                "name": s.name,
                "base_type": s.base_type.value,
                "colour": s.colour,
                "icon_key": s.icon_key.value,
                "properties": [p.key for p in s.properties],
            }
            for s in sorted(profile.stereotypes, key=lambda s: s.name)
        ],
    }


def template_view(template: DiagramTemplate) -> dict[str, Any]:
    """A template by its name and its counts: the text of its notes is the author's."""
    body = template.body
    return {
        "id": str(template.id),
        "name": template.name,
        "description": template.description,
        "slots": len(body.slots),
        "groups": len(body.groups),
        "notes": len(body.notes),
        "links": len(body.links),
    }


def stereotypes_view(refs: Sequence[StereotypeRef]) -> dict[str, Any]:
    """The labels an element carries, by name: never one of its property values."""
    return {"stereotypes": sorted(ref.name for ref in refs)}


def document_view(document: Document) -> dict[str, Any]:
    """Name, size and digest — never the text: a log is not a second copy of a runbook."""
    raw = document.content.encode()
    return {
        "id": str(document.id),
        "element_id": str(document.element_id),
        "filename": document.filename,
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def external_reference_view(reference: ExternalReference) -> dict[str, Any]:
    """The link and its state — the title only as a length and a digest.

    A title is a third party's text: the trail keeps that it changed, not what it said.
    """
    title = reference.title.encode()
    return {
        "id": str(reference.id),
        "element_id": str(reference.element_id),
        "kind": reference.kind.value,
        "target": reference.target,
        "external_id": reference.external_id,
        "status": reference.status.value,
        "title_length": len(reference.title),
        "title_sha256": hashlib.sha256(title).hexdigest(),
    }


def listed(ids: Sequence[UUID]) -> dict[str, Any]:
    """`{"count": n, "ids": [...]}` — the first `AUDIT_LISTED_IDS`, and the exact total."""
    return {"count": len(ids), "ids": [str(i) for i in ids[:AUDIT_LISTED_IDS]]}


def cascaded_view(
    *,
    relationship_count: int,
    relationships: Sequence[tuple[UUID, str]],
    document_count: int,
    document_ids: Sequence[UUID],
    diagram_count: int,
    diagram_ids: Sequence[UUID],
) -> dict[str, Any]:
    """What deleting an element took with it — one shape for every way an element is deleted.

    Exact counts, and the first `AUDIT_LISTED_IDS` of each kind.
    """
    return {
        "relationships": {
            "count": relationship_count,
            "items": [
                {"id": str(link_id), "relationship_type": kind} for link_id, kind in relationships
            ],
        },
        "documents": {"count": document_count, "ids": [str(i) for i in document_ids]},
        "diagrams": {"count": diagram_count, "ids": [str(i) for i in diagram_ids]},
    }


def package_view(package: Package) -> dict[str, Any]:
    return {
        "id": str(package.id),
        "parent_id": str(package.parent_id) if package.parent_id else None,
        "name": package.name,
        "description": package.description,
        "created_at": package.created_at.isoformat(),
        "updated_at": package.updated_at.isoformat(),
    }


def saved_query_record_view(
    *, query_id: UUID, name: str, description: str, query: Any, created_by: str
) -> dict[str, Any]:
    """From the plain columns, so a row whose JSON the code cannot read still has a view."""
    return {
        "id": str(query_id),
        "name": name,
        "description": description,
        "query": query,
        "created_by": created_by,
    }


def saved_query_view(saved: SavedQuery) -> dict[str, Any]:
    return saved_query_record_view(
        query_id=saved.id,
        name=saved.name,
        description=saved.description,
        query=to_json(saved.query),
        created_by=saved.created_by,
    )


#: The one write the model allows without an event, and it is named so that no
#: call site can reach it by omission. Re-indexing rewrites the *derived*
#: passages of a document whose text did not change (docs/adr/0019): it is not
#: a change of the model, and a trail line per document per reindex would bury
#: the changes that are.
UNAUDITED_REINDEX: Final[tuple[AuditEvent, ...]] = ()


# --- Reading the trail -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """All criteria combine with AND. `since` is inclusive, `until` exclusive."""

    entity_kind: EntityKind | None = None
    entity_id: UUID | None = None
    actor: str | None = None
    action: AuditAction | None = None
    since: datetime | None = None
    until: datetime | None = None
    request_id: str | None = None
    cursor: str | None = None
    limit: int = DEFAULT_AUDIT_PAGE


@dataclass(frozen=True, slots=True)
class AuditPage:
    events: tuple[AuditEvent, ...]
    next_cursor: str | None


def _utc(moment: datetime) -> datetime:
    """UTC, whatever the offset typed: a moment whose UTC form is out of range is an error."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def aware(moment: datetime | None) -> datetime | None:
    """A moment typed without a zone is read as UTC; comparing it to a `timestamptz` would crash."""
    if moment is None:
        return None
    try:
        return _utc(moment)
    except OverflowError as error:
        msg = "that moment is outside the dates the trail can hold"
        raise ValueError(msg) from error


def encode_cursor(at: datetime, event_id: UUID) -> str:
    raw = json.dumps([at.isoformat(), str(event_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, event_id = json.loads(base64.urlsafe_b64decode(padded))
        return _utc(datetime.fromisoformat(at)), UUID(event_id)
    except (ValueError, TypeError, binascii.Error, AttributeError, OverflowError) as error:
        msg = "the cursor is not one this server issued"
        raise InvalidCursorError(msg) from error
