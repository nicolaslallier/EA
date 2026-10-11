"""What an exchange file holds, and how its names map onto ours.

Pure: no XML, no CSV, no SQL. The ArchiMate rules stay in `domain/archimate/`
and are applied by `ArchitectureService`; this module only translates names
and carries values between a file and the service (docs/adr/0048).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.model import Element, Relationship

MAX_EXCHANGE_BYTES: Final = 20 * 1024 * 1024
EXPORT_PAGE_SIZE: Final = 1_000
EXPORT_TIMEOUT_SECONDS: Final = 300.0
#: The deployment's language; Exchange wants an `xml:lang` on every text.
EXPORT_LANGUAGE: Final = "fr"
#: One namespace for the whole 3.x family; 3.1 and 3.2 differ by schema location.
NAMESPACE: Final = "http://www.opengroup.org/xsd/archimate/3.0/"

#: The `identifier` of the `<model>` itself and the prefix of a property definition's: no
#: element or relationship may take either, or the file would hold one `xs:ID` twice.
MODEL_IDENTIFIER: Final = "id-ea-model"
PROPERTY_DEFINITION_PREFIX: Final = "propid-"

SOURCE_ID: Final = "source_id"
DESCRIPTION_KEY: Final = "ea.description"
RESERVED_PREFIX: Final = "ea."
MODIFIER_KEY: Final = "modifier"
#: Exchange's two junction types. We cannot hold one (`Element.create` refuses connectors).
JUNCTION_NAMES: Final = frozenset({"AndJunction", "OrJunction"})

_EMPTY: Mapping[str, str] = MappingProxyType({})
_NCNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_NOT_NCNAME = re.compile(r"[^A-Za-z0-9_.-]")


class ExchangeFormat(StrEnum):
    ARCHIMATE_XML = "archimate-xml"


class ScopeKind(StrEnum):
    MODEL = "model"


@dataclass(frozen=True, slots=True)
class ExportScope:
    kind: ScopeKind = ScopeKind.MODEL
    package_id: UUID | None = None
    query_id: UUID | None = None

    @property
    def label(self) -> str:
        """Used in the download's file name — fixed words, never user input."""
        return self.kind.value


@dataclass(frozen=True, slots=True)
class ExportRequest:
    format: ExchangeFormat = ExchangeFormat.ARCHIMATE_XML
    scope: ExportScope = field(default_factory=ExportScope)
    with_views: bool = True


# --- Names ---------------------------------------------------------------


def pascal_case(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))


def exchange_name(kind: ElementType | RelationshipType) -> str:
    """`business_actor` → `BusinessActor`: the `xsi:type` of Exchange."""
    return pascal_case(kind.value)


_ELEMENTS_BY_NAME: Final = {exchange_name(t): t for t in ElementType if not t.is_connector}
_RELATIONSHIPS_BY_NAME: Final = {exchange_name(t): t for t in RelationshipType}

#: `Access`, `Read`, `Write`, `ReadWrite` — derived, never restated.
ACCESS_NAMES: Final = {kind: pascal_case(kind.value) for kind in AccessType}
ACCESS_BY_NAME: Final = {name: kind for kind, name in ACCESS_NAMES.items()}


def element_type_of(name: str) -> ElementType | None:
    return _ELEMENTS_BY_NAME.get(name)


def relationship_type_of(name: str) -> RelationshipType | None:
    return _RELATIONSHIPS_BY_NAME.get(name)


# --- Identifiers ---------------------------------------------------------


def xml_identifier(raw: str) -> str:
    """An `xs:ID`: kept when it already is an NCName, otherwise `id-` + a safe form."""
    if _NCNAME.match(raw):
        return raw
    return "id-" + _NOT_NCNAME.sub("_", raw)


def identifier_of(source_id: str | None, uid: UUID) -> str:
    """What a file calls this element: its `source_id`, or a name made of its UUID."""
    return xml_identifier(source_id) if source_id else f"id-{uid}"


# --- What a file carries -------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExchangeElement:
    identifier: str
    element_type: ElementType
    name: str
    description: str = ""
    documentation: str = ""
    properties: Mapping[str, str] = _EMPTY
    #: Position in the source file (CSV line); XML reports a path instead.
    line: int | None = None
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ExchangeRelationship:
    identifier: str
    relationship_type: RelationshipType
    source: str
    target: str
    name: str = ""
    access_type: AccessType | None = None
    directed: bool = False
    properties: Mapping[str, str] = _EMPTY
    line: int | None = None
    path: str | None = None


def _without_source_id(properties: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType({k: v for k, v in properties.items() if k != SOURCE_ID})


def exchange_element_of(element: Element) -> ExchangeElement:
    return ExchangeElement(
        identifier=identifier_of(element.properties.get(SOURCE_ID), element.id),
        element_type=element.element_type,
        name=element.name,
        description=element.description,
        documentation=element.documentation,
        properties=_without_source_id(element.properties),
    )


def exchange_relationship_of(
    relationship: Relationship, *, source: str, target: str
) -> ExchangeRelationship:
    return ExchangeRelationship(
        identifier=identifier_of(relationship.properties.get(SOURCE_ID), relationship.id),
        relationship_type=relationship.relationship_type,
        source=source,
        target=target,
        name=relationship.name,
        access_type=relationship.access_type,
        directed=relationship.directed,
        properties=_without_source_id(relationship.properties),
    )
