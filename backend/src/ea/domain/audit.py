"""What the catalogue remembers about a change — see docs/adr/0044.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from ea.domain.errors import InvalidCursorError
from ea.domain.model import Element, Relationship

if TYPE_CHECKING:
    # Only for annotations: `diagrams` imports `ports`, which imports this module.
    from ea.domain.diagrams import Diagram, DiagramNode
    from ea.domain.documents import Document

MAX_AUDIT_PAGE: Final = 200
DEFAULT_AUDIT_PAGE: Final = 50


class EntityKind(StrEnum):
    ELEMENT = "element"
    RELATIONSHIP = "relationship"
    DIAGRAM = "diagram"
    DOCUMENT = "document"


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
    RELATIONSHIP_CREATED = "relationship.created"
    RELATIONSHIP_DELETED = "relationship.deleted"
    DIAGRAM_CREATED = "diagram.created"
    DIAGRAM_UPDATED = "diagram.updated"
    DIAGRAM_DELETED = "diagram.deleted"
    DIAGRAM_LAYOUT_REPLACED = "diagram.layout_replaced"
    DOCUMENT_ATTACHED = "document.attached"
    DOCUMENT_REVISED = "document.revised"
    DOCUMENT_DISCARDED = "document.discarded"

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
        if isinstance(item, Mapping) and item:
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
    }


def layout_view(nodes: Sequence[DiagramNode]) -> dict[str, Any]:
    ordered = sorted(nodes, key=lambda node: str(node.element_id))
    return {
        "nodes": [
            {
                "element_id": str(node.element_id),
                "x": node.x,
                "y": node.y,
                "width": node.width,
                "height": node.height,
            }
            for node in ordered
        ]
    }


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
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def aware(moment: datetime | None) -> datetime | None:
    """A moment typed without a zone is read as UTC; comparing it to a `timestamptz` would crash."""
    return None if moment is None else _utc(moment)


def encode_cursor(at: datetime, event_id: UUID) -> str:
    raw = json.dumps([at.isoformat(), str(event_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, event_id = json.loads(base64.urlsafe_b64decode(padded))
        return _utc(datetime.fromisoformat(at)), UUID(event_id)
    except (ValueError, TypeError, binascii.Error, AttributeError) as error:
        msg = "the cursor is not one this server issued"
        raise InvalidCursorError(msg) from error
