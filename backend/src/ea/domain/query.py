"""The structured filter of the catalogue — one object for every door.

`GET /elements`, `POST /elements/search`, the saved queries and the MCP tools
all build an `ElementQuery`; the repository compiles it into SQL in one place
(`architecture_store._matching`). It is a tree of data, never a string of SQL:
the keys and values of a property test are bound parameters downstream.

It restates no rule of ArchiMate. The element and relationship types it names
are the metamodel's enumerations, and a relationship test that no filtered type
can ever satisfy is *accepted* — the answer is empty, and the metamodel stays
the only judge of what is possible.

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from ea.domain.archimate import ElementType, Layer, RelationshipType
from ea.domain.errors import InvalidQueryError
from ea.domain.model import PROPERTY_KEY, Element

#: The version written into a stored query. A reader that meets another one
#: refuses it rather than guess what the fields meant.
QUERY_VERSION: Final = 1
MAX_PROPERTY_TESTS: Final = 10
MAX_RELATION_TESTS: Final = 5
MAX_TEXT: Final = 200
#: Properties are text (spec §11, Q7): a bound on the value tested, like the one on `text`.
MAX_PROPERTY_VALUE: Final = 200
MAX_STORED_QUERY_BYTES: Final = 16 * 1024
MAX_PAGE: Final = 200


class PropertyOp(StrEnum):
    EQ = "eq"
    CONTAINS = "contains"
    EXISTS = "exists"
    ABSENT = "absent"


class Direction(StrEnum):
    OUT = "out"
    IN = "in"
    ANY = "any"


class QueryOrder(StrEnum):
    NAME = "name"
    #: Newest first.
    CREATED_AT = "created_at"
    #: Most recently changed first.
    UPDATED_AT = "updated_at"


@dataclass(frozen=True, slots=True)
class PropertyTest:
    """One test on an element's user-defined attributes."""

    key: str
    op: PropertyOp
    value: str | None = None

    def __post_init__(self) -> None:
        if not PROPERTY_KEY.match(self.key):
            msg = (
                f"property name {self.key!r} is not a plain identifier "
                "(letters, digits and underscores, starting with a letter)"
            )
            raise ValueError(msg)
        if self.op in (PropertyOp.EQ, PropertyOp.CONTAINS):
            if self.value is None or (self.op is PropertyOp.CONTAINS and self.value == ""):
                msg = f"the {self.op.value} test on {self.key!r} needs a value"
                raise ValueError(msg)
            if len(self.value) > MAX_PROPERTY_VALUE:
                msg = f"a property value is at most {MAX_PROPERTY_VALUE} characters"
                raise ValueError(msg)
        elif self.value is not None:
            msg = f"the {self.op.value} test on {self.key!r} takes no value"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RelationTest:
    """Whether an element has (or has no) relationship of one type, optionally to one type."""

    relationship_type: RelationshipType
    direction: Direction = Direction.ANY
    present: bool = True
    other_type: ElementType | None = None


@dataclass(frozen=True, slots=True)
class ElementQuery:
    """Every criterion is combined with AND."""

    element_types: tuple[ElementType, ...] = ()
    layers: tuple[Layer, ...] = ()
    text: str | None = None
    package_id: UUID | None = None
    include_subpackages: bool = True
    root_only: bool = False
    properties: tuple[PropertyTest, ...] = ()
    relations: tuple[RelationTest, ...] = ()
    order: QueryOrder = QueryOrder.NAME
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        text = (self.text or "").strip()
        if len(text) > MAX_TEXT:
            msg = f"text is at most {MAX_TEXT} characters"
            raise ValueError(msg)
        # Frozen, so normalised through `object.__setattr__`: the stored form and
        # the compared form are then the same whoever built the query.
        object.__setattr__(self, "text", text or None)
        object.__setattr__(self, "element_types", tuple(dict.fromkeys(self.element_types)))
        object.__setattr__(self, "layers", tuple(dict.fromkeys(self.layers)))
        if len(self.properties) > MAX_PROPERTY_TESTS:
            msg = f"a query holds at most {MAX_PROPERTY_TESTS} property tests"
            raise ValueError(msg)
        if len(self.relations) > MAX_RELATION_TESTS:
            msg = f"a query holds at most {MAX_RELATION_TESTS} relation tests"
            raise ValueError(msg)
        if self.root_only and self.package_id is not None:
            msg = "root_only and package_id contradict each other"
            raise ValueError(msg)
        if not 1 <= self.limit <= MAX_PAGE:
            msg = f"limit is between 1 and {MAX_PAGE}"
            raise ValueError(msg)
        if self.offset < 0:
            msg = "offset cannot be negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class QueryResult:
    """One page of what a query matches, and how many it matches in all."""

    elements: tuple[Element, ...]
    total: int


def to_json(query: ElementQuery) -> dict[str, Any]:
    """The stored form: plain JSON, versioned, the very shape the API's schema dumps."""
    return {
        "version": QUERY_VERSION,
        "element_types": [element_type.value for element_type in query.element_types],
        "layers": [layer.value for layer in query.layers],
        "text": query.text,
        "package_id": str(query.package_id) if query.package_id is not None else None,
        "include_subpackages": query.include_subpackages,
        "root_only": query.root_only,
        "properties": [
            {"key": test.key, "op": test.op.value, "value": test.value} for test in query.properties
        ],
        "relations": [
            {
                "relationship_type": test.relationship_type.value,
                "direction": test.direction.value,
                "present": test.present,
                "other_type": test.other_type.value if test.other_type is not None else None,
            }
            for test in query.relations
        ],
        "order": query.order.value,
        "limit": query.limit,
        "offset": query.offset,
    }


def from_json(data: object) -> ElementQuery:
    """Read a stored query back, refusing what it can no longer vouch for.

    `object` and not `Mapping`: what comes out of a `jsonb` column is whatever
    was once written there, a string or `null` included.
    """
    if not isinstance(data, Mapping):
        msg = "the stored query is not an object"
        raise InvalidQueryError(msg)
    if data.get("version") != QUERY_VERSION:
        msg = f"unsupported query version: {data.get('version')!r}"
        raise InvalidQueryError(msg)
    try:
        package = data["package_id"]
        return ElementQuery(
            element_types=tuple(ElementType(value) for value in data["element_types"]),
            layers=tuple(Layer(value) for value in data["layers"]),
            text=data["text"],
            package_id=UUID(package) if package else None,
            include_subpackages=bool(data["include_subpackages"]),
            root_only=bool(data["root_only"]),
            properties=tuple(
                PropertyTest(item["key"], PropertyOp(item["op"]), item["value"])
                for item in data["properties"]
            ),
            relations=tuple(
                RelationTest(
                    RelationshipType(item["relationship_type"]),
                    Direction(item["direction"]),
                    bool(item["present"]),
                    ElementType(item["other_type"]) if item["other_type"] else None,
                )
                for item in data["relations"]
            ),
            order=QueryOrder(data["order"]),
            limit=int(data["limit"]),
            offset=int(data["offset"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        msg = f"the stored query cannot be read: {error}"
        raise InvalidQueryError(msg) from error


def check_storable(query: ElementQuery) -> None:
    """A filter is not a denial of service: the JSON kept is bounded in bytes too."""
    size = len(json.dumps(to_json(query), ensure_ascii=False).encode())
    if size > MAX_STORED_QUERY_BYTES:
        msg = f"a stored query is at most {MAX_STORED_QUERY_BYTES} bytes, this one is {size}"
        raise InvalidQueryError(msg)
