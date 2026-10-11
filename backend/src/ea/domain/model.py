"""The entities an architecture repository stores.

Both are frozen: a service that wants a changed element asks for a new one
(`rename`, `with_properties`), so nothing can mutate an object another layer is
still holding. Time is passed in rather than read, so every test is
deterministic — see `CLAUDE.md`, "no `time.sleep` — inject a clock".
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from uuid import UUID, uuid4

from ea.domain.approval import ApprovalState
from ea.domain.archimate import (
    AccessType,
    ElementType,
    RelationshipType,
    validate_relationship,
)

_EMPTY_PROPERTIES: Mapping[str, str] = MappingProxyType({})


def reject_nul(value: str, what: str) -> str:
    """PostgreSQL cannot store a NUL in `text` or `jsonb`: a refusal here, not a driver error."""
    if "\x00" in value:
        msg = f"{what} cannot contain a NUL character"
        raise ValueError(msg)
    return value


def _clean_name(name: str) -> str:
    reject_nul(name, "a name")
    cleaned = name.strip()
    if not cleaned:
        msg = "an element name cannot be blank"
        raise ValueError(msg)
    return cleaned


#: A user-defined attribute is a key of the element's `properties` map, so its
#: name has to be a plain identifier — see `db/models/architecture.py`.
PROPERTY_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}\Z")


def _freeze(properties: Mapping[str, str] | None) -> Mapping[str, str]:
    if not properties:
        return _EMPTY_PROPERTIES
    for key, value in properties.items():
        reject_nul(value, f"the property {key!r}")
        if not PROPERTY_KEY.match(key):
            msg = (
                f"property name {key!r} is not a plain identifier "
                "(letters, digits and underscores, starting with a letter)"
            )
            raise ValueError(msg)
    return MappingProxyType(dict(properties))


@dataclass(frozen=True, slots=True)
class Element:
    """A single ArchiMate concept: one application, one capability, one node."""

    id: UUID
    element_type: ElementType
    name: str
    created_at: datetime
    updated_at: datetime
    description: str = ""
    documentation: str = ""
    properties: Mapping[str, str] = field(default=_EMPTY_PROPERTIES)
    #: The package that files this element; `None` is "at the root". Not one of
    #: the element's facts: `update_element` keeps it, only a move changes it.
    package_id: UUID | None = None
    #: Review state (docs/adr/0048). Not a fact about the thing modelled, and not
    #: in `properties`: a `PATCH` that replaces the map cannot erase it.
    approval_state: ApprovalState = ApprovalState.DRAFT
    locked: bool = False
    approved_by_sub: str | None = None
    approved_at: datetime | None = None
    #: Counts the changes to the element's content (docs/adr/0048). An approval is
    #: of one revision: approving a revision someone has since changed is refused.
    revision: int = 1
    #: The subject that last changed the element (docs/adr/0048): its creation, its
    #: content, a link, a document, its filing, a restoration. Set by the
    #: repository, under the row's lock, from the actor of the event it writes —
    #: never by a client. Unknown (`None`) is not "nobody": the separation of
    #: duties refuses to approve an element whose last author is unknown.
    last_modified_by_sub: str | None = None

    @classmethod
    def create(
        cls,
        *,
        element_type: ElementType,
        name: str,
        now: datetime,
        description: str = "",
        documentation: str = "",
        properties: Mapping[str, str] | None = None,
        element_id: UUID | None = None,
    ) -> Element:
        if element_type.is_connector:
            msg = "a junction is a connector, not a modelled element"
            raise ValueError(msg)
        return cls(
            id=element_id or uuid4(),
            element_type=element_type,
            name=_clean_name(name),
            created_at=now,
            updated_at=now,
            description=reject_nul(description, "a description").strip(),
            documentation=reject_nul(documentation, "a documentation").strip(),
            properties=_freeze(properties),
        )

    def rename(self, name: str, *, now: datetime) -> Element:
        return replace(self, name=_clean_name(name), updated_at=now)

    def with_properties(self, properties: Mapping[str, str], *, now: datetime) -> Element:
        return replace(self, properties=_freeze(properties), updated_at=now)

    @property
    def layer(self) -> str:
        return self.element_type.layer.value


@dataclass(frozen=True, slots=True)
class Relationship:
    """A typed, directed link between two elements, legal by construction.

    The endpoint *types* are stored alongside the endpoint ids so a rule can be
    re-checked — during an import, say — without loading both elements again.
    """

    id: UUID
    relationship_type: RelationshipType
    source_id: UUID
    target_id: UUID
    source_type: ElementType
    target_type: ElementType
    created_at: datetime
    name: str = ""
    access_type: AccessType | None = None
    directed: bool = False
    properties: Mapping[str, str] = field(default=_EMPTY_PROPERTIES)

    @classmethod
    def between(
        cls,
        relationship_type: RelationshipType,
        source: Element,
        target: Element,
        *,
        now: datetime,
        name: str = "",
        access_type: AccessType | None = None,
        directed: bool = False,
        properties: Mapping[str, str] | None = None,
        relationship_id: UUID | None = None,
    ) -> Relationship:
        """Build a relationship, refusing anything ArchiMate does not allow."""
        validate_relationship(
            relationship_type,
            source.element_type,
            target.element_type,
            same_element=source.id == target.id,
        )
        if access_type is not None and relationship_type is not RelationshipType.ACCESS:
            msg = (
                "access_type is only meaningful on an access relationship, "
                f"not {relationship_type.value}"
            )
            raise ValueError(msg)
        if relationship_type is RelationshipType.ACCESS and access_type is None:
            access_type = AccessType.ACCESS

        return cls(
            id=relationship_id or uuid4(),
            relationship_type=relationship_type,
            source_id=source.id,
            target_id=target.id,
            source_type=source.element_type,
            target_type=target.element_type,
            created_at=now,
            name=reject_nul(name, "a name").strip(),
            access_type=access_type,
            directed=directed,
            properties=_freeze(properties),
        )
